"""
专用设备占用分配服务

稳定的分配规则（RULE_VERSION 见下，变更规则时必须递增）：

1. 排序（同优先级稳定规则）
   案件等级优先：重大(major) > 重要(important) > 一般(general)；
   同级按承诺时间早者优先；仍相同按申请时间早者优先；再相同以申请ID兜底。
   排序键唯一确定，任何重新计算产出的顺位都一致。

2. 两轮分配（部分满足规则）
   - 保障轮：按顺位依次发放各申请的最低保障量。库存不足以足额覆盖某申请的
     保障量时，该申请按剩余库存获得部分保障，保障轮随即终止，其后的申请
     本方案内不再获得任何数量。
   - 补足轮：仍按同一顺位，用剩余库存把已获保障的申请向申请量补足，
     库存耗尽即止，未获保障的申请不参与补足。
   - 结果分为 足额分配 / 部分满足 / 未分配，每条明细带可读的分配说明。
   - 方案一经确认即为各申请的最终占用：部分满足的申请按部分量占用，
     后续库存释放不会自动追加，需要时可释放后重新申请；零占用申请保持
     待分配，自动进入后续预览。

3. 预览与确认分离
   预览只生成草稿（draft），不改动任何库存和申请占用；同一设备同时只保留
   一个活跃草稿，重新预览会把旧草稿置为作废(superseded)。
   确认由管理员在单事务内复核申请状态与实时库存后，原子写入各申请的占用并
   扣减库存。已确认(confirmed)方案永不被重新计算覆盖。

4. 申请撤回 / 人工调整
   待分配申请撤回时，包含该申请的活跃草稿立即作废，需重新预览；
   人工调整只修改草稿中指定明细的数量并留痕（调整人、原因、是否低于保障量），
   不级联重算其他明细。
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AllocationItem,
    AllocationPlan,
    EquipmentApplication,
    Goods,
)

logger = logging.getLogger('apps')

RULE_VERSION = '2026-10-03-v1'

# 案件等级排序权重：数值越小优先级越高
LEVEL_ORDER = {'major': 0, 'important': 1, 'general': 2}

PLAN_DRAFT = 'draft'
PLAN_CONFIRMED = 'confirmed'
PLAN_SUPERSEDED = 'superseded'

APP_PENDING = 'pending'
APP_ALLOCATED = 'allocated'
APP_WITHDRAWN = 'withdrawn'
APP_RELEASED = 'released'


class AllocationError(Exception):
    """分配业务校验失败，消息可直接返回给调用方"""


def _q(value):
    """数量展示：去掉 Decimal 多余尾零（5.00 → 5，0.50 → 0.5）。"""
    dec = Decimal(value)
    return format(dec.normalize(), 'f') if dec != 0 else '0'


def ordered_applications(goods):
    """返回参与分配的待分配申请，按稳定排序键排序（小集合，Python 内排序）。"""
    applications = list(
        EquipmentApplication.objects.filter(goods=goods, status=APP_PENDING)
        .select_related('applicant')
    )
    applications.sort(
        key=lambda a: (
            LEVEL_ORDER.get(a.case_level, 99),
            a.committed_at,
            a.created_at,
            a.id,
        )
    )
    return applications


def occupied_quantity(goods):
    """已确认方案占用、尚未释放的数量（供展示，已确认时库存已同步扣减）。"""
    total = EquipmentApplication.objects.filter(
        goods=goods, status=APP_ALLOCATED
    ).aggregate(total=Sum('allocated_qty'))['total']
    return total or Decimal('0')


def available_quantity(goods):
    """当前可用于新分配的在手库存。

    口径与出库一致：方案确认时已从 quantity 扣减占用，释放时退回，
    因此 quantity 本身即可用库存，占用数通过 occupied_quantity 单独展示，
    不可重复扣减。
    """
    return Decimal(goods.quantity)


def _compute_rows(applications, available):
    """纯计算：输入申请列表与可用库存，输出带顺位、数量和说明的结果行。

    与数据库无关，便于单测和复用。数量统一用 Decimal。
    """
    remaining = Decimal(available)
    rows = []
    guarantee_broken = False  # 保障轮是否已在某顺位终止

    # ---- 保障轮 ----
    for rank, app in enumerate(applications, start=1):
        requested = Decimal(app.requested_qty)
        minimum = Decimal(app.minimum_qty)
        guarantee = min(minimum, requested)

        if guarantee_broken:
            guard_qty = Decimal('0')
            secured = False
            guard_note = f'顺位{rank}：保障轮在其之前已终止，本方案内不再发放数量'
        elif guarantee == 0:
            guard_qty = Decimal('0')
            secured = True
            guard_note = f'顺位{rank}：最低保障量为0，保障轮不占用库存'
        elif remaining >= guarantee:
            guard_qty = guarantee
            secured = True
            remaining -= guarantee
            guard_note = f'顺位{rank}：保障轮优先发放保障量{_q(guard_qty)}'
        else:
            # 库存不足以足额保障：拿走全部剩余库存，保障轮终止
            guard_qty = remaining
            secured = True
            remaining = Decimal('0')
            guarantee_broken = True
            guard_note = (
                f'顺位{rank}：库存仅余{_q(guard_qty)}，部分发放'
                f'{_q(guard_qty)}/{_q(guarantee)}，保障轮终止'
            )

        rows.append({
            'application': app,
            'rank': rank,
            'requested': requested,
            'minimum': minimum,
            'guard_qty': guard_qty,
            'topup_qty': Decimal('0'),
            'secured': secured,
            'guard_note': guard_note,
            'topup_note': '',
        })

    # ---- 补足轮：仅已获保障的申请，仍按同一顺位 ----
    for row in rows:
        if not row['secured']:
            row['topup_note'] = '未获保障，不参与补足轮'
            continue

        allocated_so_far = row['guard_qty']
        need = row['requested'] - allocated_so_far
        if need <= 0:
            row['topup_note'] = '保障量已覆盖申请量，无需补足'
            continue
        if remaining <= 0:
            row['topup_note'] = f'补足轮无剩余库存，仅获保障量{_q(allocated_so_far)}'
            continue

        topup = min(need, remaining)
        row['topup_qty'] = topup
        remaining -= topup
        if topup == need:
            row['topup_note'] = f'补足轮追加{_q(topup)}，补足至申请量'
        else:
            row['topup_note'] = (
                f'补足轮追加{_q(topup)}后剩余库存耗尽，未补足至申请量'
            )

    # ---- 汇总结果 ----
    result_rows = []
    for row in rows:
        allocated = row['guard_qty'] + row['topup_qty']
        if allocated >= row['requested']:
            result = 'full'
        elif allocated > 0:
            result = 'partial'
        else:
            result = 'none'
        reason = '；'.join(
            note for note in (row['guard_note'], row['topup_note']) if note
        )
        result_rows.append({
            'application': row['application'],
            'rank': row['rank'],
            'requested_qty': row['requested'],
            'minimum_qty': row['minimum'],
            'allocated_qty': allocated,
            'result': result,
            'reason': reason,
        })
    return result_rows


def _build_snapshot(applications, rows, available):
    """留存生成预览时的全部输入，供审计与解释。"""
    app_by_id = {a.id: a for a in applications}
    return {
        'rule_version': RULE_VERSION,
        'available_quantity': str(available),
        'order_rule': '案件等级(重大>重要>一般) → 承诺时间早 → 申请时间早 → 申请ID',
        'applications': [
            {
                'rank': row['rank'],
                'application_id': row['application'].id,
                'case_name': row['application'].case_name,
                'case_level': row['application'].case_level,
                'requested_qty': str(row['requested_qty']),
                'minimum_qty': str(row['minimum_qty']),
                'committed_at': app_by_id[row['application'].id].committed_at.isoformat(),
                'created_at': app_by_id[row['application'].id].created_at.isoformat(),
            }
            for row in rows
        ],
    }


@transaction.atomic
def build_plan(goods_id, created_by):
    """为某专用设备生成分配预览。旧草稿一律作废，不触碰任何已确认方案。"""
    goods = Goods.objects.select_for_update().get(pk=goods_id)

    AllocationPlan.objects.filter(goods=goods, status=PLAN_DRAFT).update(
        status=PLAN_SUPERSEDED,
        invalid_reason='重新生成预览，原草稿自动作废',
    )

    applications = ordered_applications(goods)
    available = available_quantity(goods)
    rows = _compute_rows(applications, available)

    plan = AllocationPlan.objects.create(
        goods=goods,
        status=PLAN_DRAFT,
        total_available=available,
        snapshot=_build_snapshot(applications, rows, available),
        created_by=created_by,
    )
    AllocationItem.objects.bulk_create([
        AllocationItem(
            plan=plan,
            application=row['application'],
            rank=row['rank'],
            requested_qty=row['requested_qty'],
            minimum_qty=row['minimum_qty'],
            allocated_qty=row['allocated_qty'],
            result=row['result'],
            reason=row['reason'],
        )
        for row in rows
    ])

    logger.info(
        'User %s built allocation plan %s for goods %s with %s applications, available=%s',
        getattr(created_by, 'username', None), plan.id, goods.id, len(rows), available,
    )
    return plan


@transaction.atomic
def adjust_item(plan_id, item_id, user, new_qty, adjust_reason,
                confirm_below_minimum=False):
    """人工调整草稿明细数量；不级联重算其他明细，全程留痕。"""
    plan = AllocationPlan.objects.select_for_update().get(pk=plan_id)
    if plan.status != PLAN_DRAFT:
        raise AllocationError('方案已确认或已作废，不能调整')

    item = AllocationItem.objects.select_for_update().get(pk=item_id, plan=plan)
    new_qty = Decimal(new_qty)
    if new_qty < 0:
        raise AllocationError('分配数量不能为负')
    if new_qty > item.requested_qty:
        raise AllocationError('分配数量不能超过申请数量')
    if not adjust_reason or not adjust_reason.strip():
        raise AllocationError('人工调整必须填写调整原因')
    if new_qty < item.minimum_qty and not confirm_below_minimum:
        raise AllocationError(
            '调整后数量低于最低保障量，需明确确认后方可执行'
        )

    # 全方案合计不得超过生成时锁定的可用库存
    others_total = (
        plan.items.exclude(pk=item.pk)
        .aggregate(total=Sum('allocated_qty'))['total']
    ) or Decimal('0')
    if others_total + new_qty > plan.total_available:
        raise AllocationError(
            f'调整后方案合计{others_total + new_qty}超过可用库存'
            f'{plan.total_available}，且人工调整不会自动削减其他申请'
        )

    item.allocated_qty = new_qty
    item.result = (
        'full' if new_qty == item.requested_qty else 'partial' if new_qty > 0 else 'none'
    )
    item.is_manual_adjusted = True
    item.adjusted_by = user
    item.adjust_reason = adjust_reason.strip()
    if new_qty < item.minimum_qty:
        item.reason = (
            f'人工调整至{_q(new_qty)}（低于最低保障量{_q(item.minimum_qty)}）：'
            f'{item.adjust_reason}'
        )
    else:
        item.reason = f'人工调整至{_q(new_qty)}：{item.adjust_reason}'
    item.save()

    logger.info(
        'User %s manually adjusted item %s of plan %s to %s',
        user.username, item.id, plan.id, new_qty,
    )
    return item


@transaction.atomic
def confirm_plan(plan_id, confirmed_by):
    """确认方案：复核后原子写入各申请占用并扣减库存。

    加锁顺序固定为 货物 → 方案 → 明细/申请，与 build_plan、withdraw/release
    保持同一顺序，避免并发事务成环死锁。
    """
    # 先按存在性取到货物ID（方案不存在时抛 AllocationPlan.DoesNotExist），再按固定顺序加锁
    goods_id = AllocationPlan.objects.values_list('goods_id', flat=True).get(pk=plan_id)
    goods = Goods.objects.select_for_update().get(pk=goods_id)
    plan = AllocationPlan.objects.select_for_update().get(pk=plan_id)
    if plan.status == PLAN_CONFIRMED:
        raise AllocationError('方案已确认，不能重复确认')
    if plan.status == PLAN_SUPERSEDED:
        raise AllocationError('方案已作废，请重新生成预览后再确认')

    items = list(plan.items.select_for_update().order_by('rank'))
    application_ids = [item.application_id for item in items]
    applications = {
        app.id: app
        for app in EquipmentApplication.objects.select_for_update()
        .filter(pk__in=application_ids)
    }

    # 复核 1：申请必须仍是待分配，且申请要素未被改动
    for item in items:
        app = applications.get(item.application_id)
        if app is None or app.status != APP_PENDING:
            raise AllocationError(
                f'申请“{item.application.case_name}”已撤回或状态变化，'
                f'请重新生成预览后再确认'
            )
        if (Decimal(app.requested_qty) != Decimal(item.requested_qty)
                or Decimal(app.minimum_qty) != Decimal(item.minimum_qty)):
            raise AllocationError(
                f'申请“{app.case_name}”的数量要素已变更，请重新生成预览后再确认'
            )

    # 复核 2：实时库存仍足以覆盖方案（草稿生成后库存可能已被其他途径调整）
    total_allocate = sum(
        (Decimal(item.allocated_qty) for item in items), Decimal('0')
    )
    live_available = available_quantity(goods)
    if total_allocate > live_available:
        raise AllocationError(
            f'可用库存已变化（当前{live_available}），不足以覆盖方案合计'
            f'{total_allocate}，请重新生成预览'
        )

    # 原子写入：逐申请回写占用（零占用的申请保持待分配，可参与后续方案）
    for item in items:
        app = applications[item.application_id]
        qty = Decimal(item.allocated_qty)
        app.allocated_qty = qty
        if qty > 0:
            app.status = APP_ALLOCATED
        app.save(update_fields=['allocated_qty', 'status', 'updated_at'])

    goods.quantity -= total_allocate
    goods.save(update_fields=['quantity', 'updated_at'])

    plan.status = PLAN_CONFIRMED
    plan.confirmed_by = confirmed_by
    plan.confirmed_at = timezone.now()
    plan.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'updated_at'])

    logger.info(
        'User %s confirmed allocation plan %s for goods %s, total=%s, applications=%s',
        confirmed_by.username, plan.id, goods.id, total_allocate, len(items),
    )
    return plan


@transaction.atomic
def withdraw_application(application_id, user):
    """撤回待分配申请；包含该申请的活跃草稿立即作废。"""
    goods_id = EquipmentApplication.objects.values_list(
        'goods_id', flat=True
    ).get(pk=application_id)
    # 固定加锁顺序：货物 → 申请
    goods = Goods.objects.select_for_update().get(pk=goods_id)
    app = EquipmentApplication.objects.select_for_update().get(pk=application_id)
    if app.status == APP_WITHDRAWN:
        raise AllocationError('申请已撤回，无需重复操作')
    if app.status != APP_PENDING:
        raise AllocationError('仅待分配状态的申请可以撤回，已占用请先释放')

    draft_ids = list(
        AllocationPlan.objects.filter(goods=app.goods, status=PLAN_DRAFT)
        .values_list('id', flat=True)
    )
    AllocationPlan.objects.filter(pk__in=draft_ids).update(
        status=PLAN_SUPERSEDED,
        invalid_reason=f'申请“{app.case_name}”撤回，草稿需重新生成',
    )

    app.status = APP_WITHDRAWN
    app.save(update_fields=['status', 'updated_at'])

    logger.info('User %s withdrew application %s', user.username, app.id)
    return app


@transaction.atomic
def release_application(application_id, user):
    """释放已占用设备：占用数量退回库存，申请置为已释放。"""
    goods_id = EquipmentApplication.objects.values_list(
        'goods_id', flat=True
    ).get(pk=application_id)
    # 固定加锁顺序：货物 → 申请
    goods = Goods.objects.select_for_update().get(pk=goods_id)
    app = EquipmentApplication.objects.select_for_update().get(pk=application_id)
    if app.status != APP_ALLOCATED:
        raise AllocationError('仅已占用状态的申请可以释放')
    qty = Decimal(app.allocated_qty)
    goods.quantity += qty
    goods.save(update_fields=['quantity', 'updated_at'])

    app.status = APP_RELEASED
    app.allocated_qty = Decimal('0')
    app.save(update_fields=['status', 'allocated_qty', 'updated_at'])

    logger.info('User %s released application %s, qty returned=%s', user.username, app.id, qty)
    return app
