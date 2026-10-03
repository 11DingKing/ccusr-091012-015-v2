"""
专用设备竞争分配测试

覆盖：优先级全序、最低保障两阶段分配、部分满足、预览-确认原子写入、
重新计算不覆盖已确认分配、申请撤回/归还、人工调整规则与陈旧预览保护、权限。
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from .allocation import (
    AllocationError,
    adjust_occupancy,
    adjust_preview,
    compute_plan,
    confirm_batch,
    create_preview,
    return_equipment,
    withdraw_application,
)
from .models import (
    AllocationBatch,
    AllocationItem,
    AllocationOccupancy,
    Category,
    EquipmentApplication,
    Goods,
    Unit,
    Variety,
)


def dt(day, hour=10):
    return timezone.datetime(2026, 10, day, hour, tzinfo=timezone.get_current_timezone())


class AllocationFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('alloc-admin', 'testpass123', role='admin')
        self.user = User.objects.create_user('alloc-user', 'testpass123', role='user')
        self.admin_client = self._client(self.admin)
        self.user_client = self._client(self.user)
        self.unit = Unit.objects.create(name='台', created_by=self.admin)
        self.category = Category.objects.create(name='专用设备', unit=self.unit, created_by=self.admin)
        self.variety = Variety.objects.create(name='勘查终端', category=self.category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=self.variety, name='专用勘查终端', code='SPEC-1',
            quantity=Decimal('10'),
        )

    def _client(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(user)}")
        return client

    def apply(self, case_name, level, qty, minimum, promised_day, apply_day=1, goods=None):
        return EquipmentApplication.objects.create(
            goods=goods or self.goods,
            case_name=case_name,
            case_level=level,
            applicant=self.user,
            apply_time=dt(apply_day),
            promised_time=dt(promised_day),
            quantity=Decimal(str(qty)),
            minimum_guarantee=Decimal(str(minimum)),
            status=EquipmentApplication.STATUS_WAITING,
        )

    def plan_map(self, batch):
        return {
            item.application.case_name: item
            for item in batch.items.select_related('application')
        }


class ComputePlanTest(AllocationFixture):
    def test_case_level_outrades_application_order(self):
        """低等级先申请也不能抢占：重大 > 重要 > 一般"""
        a_normal = self.apply('一般案件', 'normal', 6, 6, promised_day=5, apply_day=1)
        b_major = self.apply('重大案件', 'major', 6, 6, promised_day=10, apply_day=2)
        c_important = self.apply('重要案件', 'important', 6, 6, promised_day=3, apply_day=3)

        plans = compute_plan(Decimal('10'), [a_normal, b_major, c_important])
        by_name = {p['application'].case_name: p for p in plans}

        self.assertEqual([p['application'].case_name for p in plans],
                         ['重大案件', '重要案件', '一般案件'])
        self.assertEqual(by_name['重大案件']['proposed'], Decimal('6'))
        self.assertEqual(by_name['重大案件']['result'], AllocationItem.RESULT_FULL)
        self.assertEqual(by_name['重要案件']['proposed'], Decimal('4'))
        self.assertEqual(by_name['重要案件']['result'], AllocationItem.RESULT_PARTIAL)
        self.assertEqual(by_name['一般案件']['proposed'], Decimal('0'))
        self.assertEqual(by_name['一般案件']['result'], AllocationItem.RESULT_UNMET)
        # 每条结果都有可解释的理由
        for plan in plans:
            self.assertTrue(plan['reason'])

    def test_same_level_earlier_promise_wins_then_apply_time(self):
        """相同优先级：先按承诺归还时间，再按申请时间，结果稳定"""
        early_promise = self.apply('早承诺', 'important', 4, 4, promised_day=5, apply_day=2)
        late_promise = self.apply('晚承诺', 'important', 4, 4, promised_day=8, apply_day=1)

        plans = compute_plan(Decimal('6'), [late_promise, early_promise])
        self.assertEqual([p['application'].case_name for p in plans], ['早承诺', '晚承诺'])
        self.assertEqual(plans[0]['proposed'], Decimal('4'))
        self.assertEqual(plans[1]['proposed'], Decimal('2'))

        same_a = self.apply('同条件A', 'major', 4, 4, promised_day=9, apply_day=3)
        same_b = self.apply('同条件B', 'major', 4, 4, promised_day=9, apply_day=3)
        plans = compute_plan(Decimal('6'), [same_b, same_a])
        # 完全同条件时以申请ID兜底，输入顺序不影响结果
        self.assertEqual([p['application'].case_name for p in plans], ['同条件A', '同条件B'])

    def test_two_phase_guarantee_then_leftover_top_up(self):
        """第一阶段保最低量，剩余库存再按顺位追加"""
        major = self.apply('重大少量', 'major', 2, 1, promised_day=8)
        important = self.apply('重要大量', 'important', 10, 5, promised_day=9)

        plans = compute_plan(Decimal('10'), [important, major])
        by_name = {p['application'].case_name: p for p in plans}
        # 重大先保1、重要保5；剩余4先给重大补足到2，再给重要追加3
        self.assertEqual(by_name['重大少量']['proposed'], Decimal('2'))
        self.assertEqual(by_name['重要大量']['proposed'], Decimal('8'))
        self.assertEqual(by_name['重要大量']['result'], AllocationItem.RESULT_PARTIAL)


class PreviewConfirmFlowTest(AllocationFixture):
    def test_preview_then_confirm_writes_occupancies_atomically(self):
        self.apply('重大案件', 'major', 6, 6, promised_day=10)
        self.apply('一般案件', 'normal', 6, 6, promised_day=5)

        batch = create_preview(self.goods.id, self.admin)
        self.assertEqual(batch.status, AllocationBatch.STATUS_DRAFT)
        self.assertEqual(batch.available_snapshot, Decimal('10'))
        self.assertEqual(batch.occupied_snapshot, Decimal('0'))

        # 预览阶段不产生任何占用
        self.assertEqual(AllocationOccupancy.objects.count(), 0)

        confirm_batch(batch.id, self.admin)
        batch.refresh_from_db()
        self.assertEqual(batch.status, AllocationBatch.STATUS_CONFIRMED)
        self.assertEqual(batch.confirmed_by, self.admin)

        items = self.plan_map(batch)
        # 两阶段：重大足额6，一般按最低保障分到剩余4（部分满足）
        self.assertEqual(
            AllocationOccupancy.objects.get(application__case_name='重大案件').quantity,
            Decimal('6'),
        )
        self.assertEqual(
            AllocationOccupancy.objects.get(application__case_name='一般案件').quantity,
            Decimal('4'),
        )
        items['重大案件'].application.refresh_from_db()
        items['一般案件'].application.refresh_from_db()
        self.assertEqual(items['重大案件'].application.status, EquipmentApplication.STATUS_ALLOCATED)
        self.assertEqual(items['一般案件'].application.status, EquipmentApplication.STATUS_ALLOCATED)

    def test_confirm_is_idempotent_guard_and_duplicate_rejected(self):
        self.apply('案件A', 'major', 3, 3, promised_day=5)
        batch = create_preview(self.goods.id, self.admin)
        confirm_batch(batch.id, self.admin)
        with self.assertRaises(AllocationError) as ctx:
            confirm_batch(batch.id, self.admin)
        self.assertEqual(ctx.exception.code, 409)

    def test_recalc_never_overwrites_confirmed_allocation(self):
        """重新计算只产生新预览，已确认占用原封不动"""
        major = self.apply('重大案件', 'major', 6, 6, promised_day=10)
        batch1 = create_preview(self.goods.id, self.admin)
        confirm_batch(batch1.id, self.admin)

        # 新案件进入，重新计算
        self.apply('后到重要案件', 'important', 6, 6, promised_day=6)
        batch2 = create_preview(self.goods.id, self.admin, source=AllocationBatch.SOURCE_RECALC)
        self.assertNotEqual(batch1.id, batch2.id)

        # 历史批次不可变
        batch1.refresh_from_db()
        self.assertEqual(batch1.status, AllocationBatch.STATUS_CONFIRMED)
        major.refresh_from_db()
        self.assertEqual(major.status, EquipmentApplication.STATUS_ALLOCATED)
        self.assertEqual(
            AllocationOccupancy.objects.get(application=major).quantity, Decimal('6')
        )
        # 新预览基于剩余库存，只含未决申请
        items = self.plan_map(batch2)
        self.assertNotIn('重大案件', items)
        self.assertEqual(items['后到重要案件'].proposed_quantity, Decimal('4'))
        self.assertEqual(batch2.available_snapshot, Decimal('4'))
        self.assertEqual(batch2.occupied_snapshot, Decimal('6'))

    def test_stale_preview_confirm_rejected_not_silently_written(self):
        """预览生成后输入变化（新申请登记），确认必须 409 而不是悄悄覆盖"""
        self.apply('重大案件', 'major', 6, 6, promised_day=10)
        batch = create_preview(self.goods.id, self.admin)

        self.apply('插队重要案件', 'important', 6, 6, promised_day=5)
        with self.assertRaises(AllocationError) as ctx:
            confirm_batch(batch.id, self.admin)
        self.assertEqual(ctx.exception.code, 409)
        batch.refresh_from_db()
        self.assertEqual(batch.status, AllocationBatch.STATUS_EXPIRED)
        self.assertEqual(AllocationOccupancy.objects.count(), 0)

        # 重新生成后可正常确认
        new_batch = create_preview(self.goods.id, self.admin)
        confirm_batch(new_batch.id, self.admin)
        self.assertEqual(AllocationOccupancy.objects.count(), 2)


class ManualAdjustmentTest(AllocationFixture):
    def test_adjust_preview_within_pool(self):
        self.apply('案件A', 'major', 6, 2, promised_day=10)
        self.apply('案件B', 'important', 6, 2, promised_day=5)
        batch = create_preview(self.goods.id, self.admin)

        items = self.plan_map(batch)
        a_id, b_id = items['案件A'].application_id, items['案件B'].application_id
        # 管理员在总量与申请量内重新切分：A 3, B 6（合计9 ≤ 10）
        adjust_preview(batch.id, self.admin, {a_id: '3', b_id: '6'})
        items = self.plan_map(batch)
        self.assertEqual(items['案件A'].final_quantity, Decimal('3'))
        self.assertEqual(items['案件B'].final_quantity, Decimal('6'))
        self.assertTrue(items['案件A'].manual_overridden)
        self.assertEqual(items['案件A'].result, AllocationItem.RESULT_PARTIAL)
        self.assertIn('人工调整', items['案件A'].reason)

    def test_adjust_rejected_over_pool_or_bounds(self):
        self.apply('案件A', 'major', 6, 6, promised_day=10)
        batch = create_preview(self.goods.id, self.admin)
        a_id = self.plan_map(batch)['案件A'].application_id

        with self.assertRaises(AllocationError):
            adjust_preview(batch.id, self.admin, {a_id: '7'})  # 超申请量
        with self.assertRaises(AllocationError):
            adjust_preview(batch.id, self.admin, {99999: '1'})  # 不在预览内
        with self.assertRaises(AllocationError):
            adjust_preview(batch.id, self.admin, {a_id: '-1'})

        confirm_batch(batch.id, self.admin)
        with self.assertRaises(AllocationError) as ctx:
            adjust_preview(batch.id, self.admin, {a_id: '2'})  # 已确认不可调
        self.assertEqual(ctx.exception.code, 409)

    def test_post_confirm_occupancy_adjust(self):
        app = self.apply('案件A', 'major', 6, 6, promised_day=10)
        batch = create_preview(self.goods.id, self.admin)
        confirm_batch(batch.id, self.admin)

        adjust_occupancy(app.id, self.admin, '4')
        self.assertEqual(AllocationOccupancy.objects.get(application=app).quantity, Decimal('4'))

        # 超过总库存拒绝
        with self.assertRaises(AllocationError):
            adjust_occupancy(app.id, self.admin, '11')

        # 调为 0 释放
        adjust_occupancy(app.id, self.admin, '0')
        self.assertFalse(AllocationOccupancy.objects.filter(application=app).exists())
        app.refresh_from_db()
        self.assertEqual(app.status, EquipmentApplication.STATUS_RETURNED)

        # 历史确认批次仍然不可变
        batch.refresh_from_db()
        self.assertEqual(batch.status, AllocationBatch.STATUS_CONFIRMED)
        self.assertEqual(self.plan_map(batch)['案件A'].final_quantity, Decimal('6'))


class WithdrawReturnTest(AllocationFixture):
    def test_withdraw_waiting_application_expires_draft(self):
        app = self.apply('案件A', 'major', 6, 6, promised_day=10)
        batch = create_preview(self.goods.id, self.admin)
        withdraw_application(app.id, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.status, AllocationBatch.STATUS_EXPIRED)
        app.refresh_from_db()
        self.assertEqual(app.status, EquipmentApplication.STATUS_WITHDRAWN)

        # 撤回后重新预览不再包含该案件
        new_batch = create_preview(self.goods.id, self.admin)
        self.assertEqual(new_batch.items.count(), 0)

    def test_withdraw_allocated_releases_resource_and_recalc_uses_it(self):
        # 重大案件申请量=库存，一般案件最低保障完全无法满足（unmet）
        major = self.apply('重大案件', 'major', 10, 10, promised_day=10)
        normal = self.apply('一般案件', 'normal', 6, 6, promised_day=5)
        batch = create_preview(self.goods.id, self.admin)
        self.assertEqual(self.plan_map(batch)['一般案件'].result, AllocationItem.RESULT_UNMET)
        confirm_batch(batch.id, self.admin)

        # 撤回重大案件释放全部库存，重算后一般案件足额
        withdraw_application(major.id, self.user)
        self.assertFalse(AllocationOccupancy.objects.filter(application=major).exists())
        major.refresh_from_db()
        self.assertEqual(major.status, EquipmentApplication.STATUS_WITHDRAWN)

        new_batch = create_preview(self.goods.id, self.admin)
        items = self.plan_map(new_batch)
        self.assertEqual(items['一般案件'].proposed_quantity, Decimal('6'))
        confirm_batch(new_batch.id, self.admin)
        self.assertEqual(
            AllocationOccupancy.objects.get(application=normal).quantity, Decimal('6')
        )

    def test_return_equipment(self):
        app = self.apply('案件A', 'major', 6, 6, promised_day=10)
        batch = create_preview(self.goods.id, self.admin)
        confirm_batch(batch.id, self.admin)

        return_equipment(app.id, self.user)
        self.assertFalse(AllocationOccupancy.objects.filter(application=app).exists())
        app.refresh_from_db()
        self.assertEqual(app.status, EquipmentApplication.STATUS_RETURNED)
        with self.assertRaises(AllocationError):
            return_equipment(app.id, self.user)


class AllocationAPITest(AllocationFixture):
    def _payload(self, case_name, level, qty, minimum, promised='2026-10-10T10:00:00+08:00'):
        return {
            'goods': self.goods.id,
            'case_name': case_name,
            'case_level': level,
            'apply_time': '2026-10-01T09:00:00+08:00',
            'promised_time': promised,
            'quantity': qty,
            'minimum_guarantee': minimum,
        }

    def test_apply_validate_inputs(self):
        bad_time = self.user_client.post(
            '/api/equipment-applications/',
            {**self._payload('案件', 'major', 6, 6), 'promised_time': '2026-09-01T09:00:00+08:00'},
            format='json',
        )
        self.assertEqual(bad_time.status_code, 400)
        bad_min = self.user_client.post(
            '/api/equipment-applications/',
            self._payload('案件', 'major', 6, 8),
            format='json',
        )
        self.assertEqual(bad_min.status_code, 400)
        ok = self.user_client.post(
            '/api/equipment-applications/',
            self._payload('重大案件', 'major', 6, 6),
            format='json',
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['data']['status'], 'waiting')

    def test_end_to_end_preview_adjust_confirm_permissions(self):
        # 两起竞争案件
        self.user_client.post('/api/equipment-applications/',
                              self._payload('重大案件', 'major', 6, 6, '2026-10-10T10:00:00+08:00'), format='json')
        self.user_client.post('/api/equipment-applications/',
                              self._payload('一般案件', 'normal', 6, 6, '2026-10-05T10:00:00+08:00'), format='json')

        # 普通用户可以生成预览
        resp = self.user_client.post(f'/api/goods/{self.goods.id}/allocation/preview/', {}, format='json')
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()['data']['id']
        results = [(i['application']['case_name'], i['result'], str(i['final_quantity']))
                   for i in resp.json()['data']['items']]
        self.assertEqual(results[0][0], '重大案件')
        self.assertEqual(results[0][2], '6.00')
        # 两阶段：一般案件获得剩余 4，部分满足
        self.assertEqual(results[1][0], '一般案件')
        self.assertEqual(results[1][1], 'partial')
        self.assertEqual(results[1][2], '4.00')

        # 普通用户不能人工调整/确认
        denied = self.user_client.post(
            f'/api/allocation/batches/{batch_id}/confirm/', {}, format='json'
        )
        self.assertEqual(denied.status_code, 403)

        # 管理员调整为 5/5
        items = resp.json()['data']['items']
        adjust = self.admin_client.post(
            f'/api/allocation/batches/{batch_id}/adjust/',
            {'items': [
                {'application': items[0]['application_id'], 'quantity': '5'},
                {'application': items[1]['application_id'], 'quantity': '5'},
            ]},
            format='json',
        )
        self.assertEqual(adjust.status_code, 200)

        # 超总量调整被拒
        over = self.admin_client.post(
            f'/api/allocation/batches/{batch_id}/adjust/',
            {'items': [{'application': items[0]['application_id'], 'quantity': '10'}]},
            format='json',
        )
        self.assertEqual(over.status_code, 400)

        # 管理员确认
        confirmed = self.admin_client.post(
            f'/api/allocation/batches/{batch_id}/confirm/', {}, format='json'
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['data']['status'], 'confirmed')

        # 已确认批次再调整 409
        again = self.admin_client.post(
            f'/api/allocation/batches/{batch_id}/adjust/',
            {'items': [{'application': items[0]['application_id'], 'quantity': '1'}]},
            format='json',
        )
        self.assertEqual(again.status_code, 409)

    def test_withdraw_api_and_logs(self):
        resp = self.user_client.post(
            '/api/equipment-applications/',
            self._payload('重大案件', 'major', 6, 6), format='json',
        )
        app_id = resp.json()['data']['id']
        self.admin_client.post(f'/api/goods/{self.goods.id}/allocation/preview/', {}, format='json')

        withdrawn = self.user_client.post(f'/api/equipment-applications/{app_id}/withdraw/', {}, format='json')
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(withdrawn.json()['data']['status'], 'withdrawn')

        logs = self.user_client.get('/api/allocation/logs/').json()['data']['list']
        actions = {log['action'] for log in logs}
        self.assertIn('calculate', actions)
        self.assertIn('withdraw', actions)
        self.assertIn('recalc', actions)
