"""
专用设备竞争分配引擎

规则（全序、稳定、可解释）：
1. 排序键：案件等级权重（重大>重要>一般）→ 承诺归还时间早 → 申请时间早 → 申请ID升序；
2. 两阶段分配：第一阶段按顺位保最低保障量，第二阶段将剩余库存按顺位追加至申请量；
3. 已确认占用是不可变事实：重新计算只产生新的 draft 批次，绝不回写历史批次与占用；
4. 预览携带输入数据指纹，确认时若申请集合/数量/库存已变化则整体失败（409），
   绝不把陈旧结果静默写入；
5. 撤回、归还、人工调整全部留痕（AllocationAdjustmentLog）。
"""
import hashlib
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AllocationAdjustmentLog,
    AllocationBatch,
    AllocationItem,
    AllocationOccupancy,
    CASE_LEVEL_WEIGHTS,
    EquipmentApplication,
    Goods,
)


class AllocationError(Exception):
    """分配业务异常，message 直接返回给调用方"""

    def __init__(self, message, code=400):
        self.message = message
        self.code = code
        super().__init__(message)


# ==================== 纯算法部分 ====================

def priority_key(application):
    """全序排序键：等级(高优先) → 承诺归还(早优先) → 申请时间(早优先) → ID"""
    return (
        -application.level_weight,
        application.promised_time,
        application.apply_time,
        application.pk,
    )


def _level_label(application):
    return dict(EquipmentApplication._meta.get_field('case_level').choices)[application.case_level]


def compute_plan(available, applications):
    """对一批申请计算分配方案（纯函数）。

    :param available: 可分配库存（总库存 - 已确认占用），Decimal
    :param applications: EquipmentApplication 可迭代对象（status=waiting）
    :return: list[dict]，每项含 application/rank/proposed/result/reason
    """
    pool = Decimal(available).quantize(Decimal('0.01'))
    if pool < 0:
        pool = Decimal('0')

    ordered = sorted(applications, key=priority_key)
    rows = []
    # 第一阶段实际发放量
    first_phase = {}

    for rank, app in enumerate(ordered, start=1):
        guarantee = min(app.minimum_guarantee, app.quantity)
        grant = min(guarantee, pool)
        first_phase[app.pk] = grant
        pool -= grant
        rows.append({
            'application': app,
            'rank': rank,
            'first_phase': grant,
            'guarantee': guarantee,
        })

    # 第二阶段：剩余库存按同一顺位追加至申请量
    for row in rows:
        app = row['application']
        held = first_phase[app.pk]
        if held < app.quantity and pool > 0:
            add = min(app.quantity - held, pool)
            held += add
            pool -= add
        row['proposed'] = held

    plans = []
    for row in rows:
        app = row['application']
        proposed = row['proposed']
        guarantee = row['guarantee']
        first = row['first_phase']
        if proposed >= app.quantity:
            result = AllocationItem.RESULT_FULL
            reason = (
                f"顺位{row['rank']}（{_level_label(app)}，承诺归还"
                f"{app.promised_time.strftime('%Y-%m-%d %H:%M')}）：库存充足，足额满足申请量"
            )
        elif proposed == 0:
            result = AllocationItem.RESULT_UNMET
            reason = (
                f"顺位{row['rank']}（{_level_label(app)}，承诺归还"
                f"{app.promised_time.strftime('%Y-%m-%d %H:%M')}）："
                f"库存已被更高顺位案件用尽，最低保障量{guarantee}亦无法满足"
            )
        else:
            result = AllocationItem.RESULT_PARTIAL
            parts = [
                f"顺位{row['rank']}（{_level_label(app)}，承诺归还"
                f"{app.promised_time.strftime('%Y-%m-%d %H:%M')}）：库存不足，部分满足",
                f"第一阶段保障最低量{first}",
            ]
            extra = proposed - first
            if extra > 0:
                parts.append(f"剩余库存按顺位追加{extra}")
            elif proposed < guarantee:
                parts.append(f"库存耗尽，保障量{guarantee}未达")
            reason = '；'.join(parts)
        plans.append({
            'application': app,
            'rank': row['rank'],
            'proposed': proposed,
            'result': result,
            'reason': reason,
        })
    return plans


# ==================== 快照与指纹 ====================

def _occupied_total(goods_id):
    total = AllocationOccupancy.objects.filter(goods_id=goods_id) \
        .aggregate(total=Sum('quantity'))['total']
    return total or Decimal('0')


def _waiting_fingerprint(goods, occupied_total):
    """对"会影响分配结果的全部输入"取指纹。

    总库存、已确认占用总量、全部 waiting 申请的关键属性任何一项变化，
    指纹都不同 —— 用于确认时拒绝陈旧预览。
    """
    parts = [f"goods#{goods.pk}:q={goods.quantity}:occ={occupied_total}"]
    apps = EquipmentApplication.objects.filter(
        goods=goods, status=EquipmentApplication.STATUS_WAITING
    ).order_by('pk').values_list(
        'pk', 'case_level', 'quantity', 'minimum_guarantee',
        'promised_time', 'apply_time', 'updated_at',
    )
    for row in apps:
        parts.append('|'.join(str(v) for v in row))
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


# ==================== 批次生命周期服务 ====================

def _expire_drafts(goods_id, acting_user, why):
    """申请/库存发生变化时，将未确认预览置为失效（不删除、可审计）"""
    drafts = AllocationBatch.objects.filter(goods_id=goods_id, status=AllocationBatch.STATUS_DRAFT)
    for batch in drafts:
        batch.status = AllocationBatch.STATUS_EXPIRED
        batch.save(update_fields=['status', 'updated_at'])
        AllocationAdjustmentLog.objects.create(
            batch=batch, operator=acting_user,
            action=AllocationAdjustmentLog.ACTION_RECALC,
            detail=f'预览因{why}失效，需重新生成',
        )


def invalidate_drafts(goods_id, acting_user, why):
    """供申请登记/库存变动等外部场景调用的预览失效入口"""
    _expire_drafts(goods_id, acting_user, why)


def create_preview(goods_id, acting_user, source=AllocationBatch.SOURCE_MANUAL):
    """生成（或重新生成）某专用设备的分配预览，不落任何占用。"""
    with transaction.atomic():
        try:
            goods = Goods.objects.select_for_update().get(pk=goods_id)
        except Goods.DoesNotExist:
            raise AllocationError('专用设备不存在', code=404)

        # 同一设备同时只保留一个有效预览：旧预览失效，绝不静默改写
        _expire_drafts(goods_id, acting_user, '重新计算' if source == AllocationBatch.SOURCE_RECALC else '生成新预览')

        applications = list(EquipmentApplication.objects.filter(
            goods=goods, status=EquipmentApplication.STATUS_WAITING
        ))
        occupied_total = _occupied_total(goods_id)
        available = goods.quantity - occupied_total

        plans = compute_plan(available, applications)

        batch = AllocationBatch.objects.create(
            goods=goods,
            source=source,
            available_snapshot=available,
            occupied_snapshot=occupied_total,
            input_fingerprint=_waiting_fingerprint(goods, occupied_total),
            created_by=acting_user,
        )
        items = [
            AllocationItem(
                batch=batch,
                application=plan['application'],
                rank=plan['rank'],
                proposed_quantity=plan['proposed'],
                final_quantity=plan['proposed'],
                result=plan['result'],
                reason=plan['reason'],
            )
            for plan in plans
        ]
        AllocationItem.objects.bulk_create(items)

        AllocationAdjustmentLog.objects.create(
            batch=batch, operator=acting_user,
            action=AllocationAdjustmentLog.ACTION_CALCULATE,
            detail=(
                f"按案件等级/承诺归还/申请时间排序，对{len(plans)}起案件生成预览；"
                f"可分配库存{available}（总库存{goods.quantity}-已占用{occupied_total}）"
            ),
        )
    return batch


def adjust_preview(batch_id, acting_user, adjustments):
    """有权限人员在确认前对预览做整单人工调整。

    :param adjustments: {application_id: Decimal 数量}，仅接受批次内已有的申请行；
                        新增/撤出的申请须重新生成预览。
    稳定约束：数量 ∈ [0, 申请量]；全单合计 ≤ 预览时可分配库存。
    """
    with transaction.atomic():
        try:
            batch = AllocationBatch.objects.select_for_update().get(pk=batch_id)
        except AllocationBatch.DoesNotExist:
            raise AllocationError('分配批次不存在', code=404)
        if not batch.is_editable:
            raise AllocationError('该预览已确认或已失效，不能调整', code=409)

        items = list(batch.items.select_related('application'))
        item_map = {item.application_id: item for item in items}

        unknown = [str(k) for k in adjustments if k not in item_map]
        if unknown:
            raise AllocationError(f"申请 {'、'.join(unknown)} 不在当前预览内，变动后请重新生成预览")

        for app_id, raw_qty in adjustments.items():
            qty = _quantize(raw_qty)
            item = item_map[app_id]
            if qty < 0 or qty > item.application.quantity:
                raise AllocationError(
                    f"案件「{item.application.case_name}」调整量须在 0 ~ {item.application.quantity} 之间"
                )

        adjusted_total = sum(
            (Decimal(adjustments.get(item.application_id, item.final_quantity)) for item in items),
            Decimal('0'),
        )
        if adjusted_total > batch.available_snapshot:
            raise AllocationError(
                f"调整后合计{adjusted_total}超过可分配库存{batch.available_snapshot}"
            )

        for app_id, raw_qty in adjustments.items():
            qty = _quantize(raw_qty)
            item = item_map[app_id]
            before = item.final_quantity
            if qty == before:
                continue
            item.final_quantity = qty
            item.manual_overridden = True
            item.adjusted_by = acting_user
            item.result = _result_for(qty, item.application.quantity)
            item.reason = f"{item.reason.split('；已由管理员人工调整')[0]}；已由管理员人工调整"
            item.save()
            AllocationAdjustmentLog.objects.create(
                batch=batch, application=item.application, operator=acting_user,
                action=AllocationAdjustmentLog.ACTION_MANUAL_ADJUST,
                before_quantity=before, after_quantity=qty,
                detail=f"预览人工调整：{before} → {qty}",
            )
    return batch


def _result_for(qty, applied):
    if qty <= 0:
        return AllocationItem.RESULT_UNMET
    if qty < applied:
        return AllocationItem.RESULT_PARTIAL
    return AllocationItem.RESULT_FULL


def confirm_batch(batch_id, acting_user):
    """确认预览：指纹校验通过后，在一个事务内原子写入全部占用。"""
    stale = False
    with transaction.atomic():
        try:
            batch = AllocationBatch.objects.select_for_update().get(pk=batch_id)
        except AllocationBatch.DoesNotExist:
            raise AllocationError('分配批次不存在', code=404)
        if batch.status == AllocationBatch.STATUS_CONFIRMED:
            raise AllocationError('该批次已确认，请勿重复操作', code=409)
        if batch.status == AllocationBatch.STATUS_EXPIRED:
            raise AllocationError('该预览已失效，请重新生成预览后再确认', code=409)

        goods = Goods.objects.select_for_update().get(pk=batch.goods_id)
        occupied_total = _occupied_total(goods.pk)

        # 乐观并发：输入变了就整体拒绝，不允许陈旧分配静默落库。
        # 注意：失效动作必须在事务外提交，否则会随异常一起回滚。
        current_fingerprint = _waiting_fingerprint(goods, occupied_total)
        if current_fingerprint != batch.input_fingerprint:
            stale = True
        else:
            items = list(batch.items.select_related('application').order_by('rank'))
            if not items:
                raise AllocationError('当前没有可确认的分配行')

            final_total = sum((item.final_quantity for item in items), Decimal('0'))
            if final_total > batch.available_snapshot:
                raise AllocationError(
                    f"分配合计{final_total}超过可分配库存{batch.available_snapshot}",
                    code=409,
                )

            # 防御性二次校验：任一申请已在别处占用则整体失败回滚
            app_ids = [item.application_id for item in items]
            if AllocationOccupancy.objects.filter(application_id__in=app_ids).exists():
                raise AllocationError('存在已确认占用的申请，请重新生成预览', code=409)

            occupancies = [
                AllocationOccupancy(
                    application=item.application,
                    batch=batch,
                    goods=goods,
                    quantity=item.final_quantity,
                )
                for item in items if item.final_quantity > 0
            ]
            AllocationOccupancy.objects.bulk_create(occupancies)

            granted_ids = [item.application_id for item in items if item.final_quantity > 0]
            EquipmentApplication.objects.filter(pk__in=granted_ids).update(
                status=EquipmentApplication.STATUS_ALLOCATED
            )

            batch.status = AllocationBatch.STATUS_CONFIRMED
            batch.confirmed_at = timezone.now()
            batch.confirmed_by = acting_user
            batch.save()

            for item in items:
                AllocationAdjustmentLog.objects.create(
                    batch=batch, application=item.application, operator=acting_user,
                    action=AllocationAdjustmentLog.ACTION_CONFIRM,
                    after_quantity=item.final_quantity,
                    detail=(
                        f"确认写入占用{item.final_quantity}"
                        + ('（人工调整结果）' if item.manual_overridden else '（算法建议结果）')
                    ),
                )

    if stale:
        # 独立事务提交失效标记与留痕，再向调用方报错
        with transaction.atomic():
            AllocationBatch.objects.filter(pk=batch_id).update(
                status=AllocationBatch.STATUS_EXPIRED, updated_at=timezone.now()
            )
            AllocationAdjustmentLog.objects.create(
                batch_id=batch_id, operator=acting_user,
                action=AllocationAdjustmentLog.ACTION_RECALC,
                detail='确认时检测到申请数据或库存已变化，预览置为失效',
            )
        raise AllocationError(
            '申请数据或库存自预览生成后已变化，预览已失效，请重新生成预览',
            code=409,
        )
    return batch


def withdraw_application(application_id, acting_user):
    """撤回申请：等待中直接撤回；已占用则同步释放资源。其他未确认预览一律失效。"""
    with transaction.atomic():
        try:
            app = EquipmentApplication.objects.select_for_update() \
                .select_related('goods').get(pk=application_id)
        except EquipmentApplication.DoesNotExist:
            raise AllocationError('申请不存在', code=404)

        if app.status in (EquipmentApplication.STATUS_WITHDRAWN, EquipmentApplication.STATUS_RETURNED):
            raise AllocationError('该申请已结束，无需撤回')

        before_qty = None
        occupancy = AllocationOccupancy.objects.filter(application=app).first()
        if occupancy is not None:
            before_qty = occupancy.quantity
            occupancy.delete()

        app.status = EquipmentApplication.STATUS_WITHDRAWN
        app.save(update_fields=['status', 'updated_at'])

        _expire_drafts(app.goods_id, acting_user, '申请撤回')
        AllocationAdjustmentLog.objects.create(
            application=app, operator=acting_user,
            action=AllocationAdjustmentLog.ACTION_WITHDRAW,
            before_quantity=before_qty, after_quantity=Decimal('0'),
            detail='申请撤回，资源释放' if before_qty is not None else '申请撤回（尚未占用）',
        )
    return app


def return_equipment(application_id, acting_user):
    """已占用设备归还：删除占用、释放资源、未确认预览失效。"""
    with transaction.atomic():
        try:
            app = EquipmentApplication.objects.select_for_update() \
                .select_related('goods').get(pk=application_id)
        except EquipmentApplication.DoesNotExist:
            raise AllocationError('申请不存在', code=404)

        occupancy = AllocationOccupancy.objects.filter(application=app).first()
        if occupancy is None:
            raise AllocationError('该申请没有占用中的设备')

        before_qty = occupancy.quantity
        occupancy.delete()
        app.status = EquipmentApplication.STATUS_RETURNED
        app.save(update_fields=['status', 'updated_at'])

        _expire_drafts(app.goods_id, acting_user, '设备归还')
        AllocationAdjustmentLog.objects.create(
            application=app, operator=acting_user,
            action=AllocationAdjustmentLog.ACTION_RETURN,
            before_quantity=before_qty, after_quantity=Decimal('0'),
            detail=f'设备归还，释放占用{before_qty}',
        )
    return app


def adjust_occupancy(application_id, acting_user, new_quantity):
    """确认后的人工调整：在库存约束内修改占用量；调为 0 视同释放。

    已确认批次本身保持不可变，调整只作用于当前占用并单独留痕。
    """
    new_quantity = _quantize(new_quantity)
    with transaction.atomic():
        try:
            app = EquipmentApplication.objects.select_for_update() \
                .select_related('goods').get(pk=application_id)
        except EquipmentApplication.DoesNotExist:
            raise AllocationError('申请不存在', code=404)

        try:
            occupancy = AllocationOccupancy.objects.select_for_update() \
                .select_related('batch').get(application=app)
        except AllocationOccupancy.DoesNotExist:
            raise AllocationError('该申请没有已确认的占用')

        if new_quantity < 0 or new_quantity > app.quantity:
            raise AllocationError(f"调整量须在 0 ~ {app.quantity} 之间")

        others = AllocationOccupancy.objects.filter(goods=app.goods) \
            .exclude(pk=occupancy.pk) \
            .aggregate(total=Sum('quantity'))['total'] \
            or Decimal('0')
        if others + new_quantity > app.goods.quantity:
            raise AllocationError(
                f"调整后全设备占用{others + new_quantity}超过总库存{app.goods.quantity}"
            )

        before = occupancy.quantity
        if new_quantity == 0:
            batch = occupancy.batch
            occupancy.delete()
            app.status = EquipmentApplication.STATUS_RETURNED
            app.save(update_fields=['status', 'updated_at'])
            AllocationAdjustmentLog.objects.create(
                batch=batch, application=app, operator=acting_user,
                action=AllocationAdjustmentLog.ACTION_MANUAL_ADJUST,
                before_quantity=before, after_quantity=Decimal('0'),
                detail='确认后人工调整为0，占用释放',
            )
            _expire_drafts(app.goods_id, acting_user, '占用人工调整')
        elif new_quantity != before:
            occupancy.quantity = new_quantity
            occupancy.save(update_fields=['quantity', 'updated_at'])
            AllocationAdjustmentLog.objects.create(
                batch=occupancy.batch, application=app, operator=acting_user,
                action=AllocationAdjustmentLog.ACTION_MANUAL_ADJUST,
                before_quantity=before, after_quantity=new_quantity,
                detail=f'确认后人工调整：{before} → {new_quantity}',
            )
            _expire_drafts(app.goods_id, acting_user, '占用人工调整')
    return app


def _quantize(value):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except Exception:
        raise AllocationError('数量格式不正确')
