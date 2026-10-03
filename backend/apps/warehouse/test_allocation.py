"""
专用设备占用分配测试：稳定规则、预览/确认分离、撤回与人工调整
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User

from .allocation import (
    AllocationError,
    RULE_VERSION,
    _compute_rows,
    adjust_item,
    build_plan,
    confirm_plan,
    release_application,
    withdraw_application,
)
from .models import (
    AllocationItem,
    AllocationPlan,
    Category,
    EquipmentApplication,
    Goods,
    Unit,
    Variety,
)


class AllocationFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('alloc-admin', 'testpass123', role='admin')
        self.user = User.objects.create_user('alloc-user', 'testpass123', role='user')
        self.admin_client = APIClient()
        self.admin_client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(self.admin)}')
        self.user_client = APIClient()
        self.user_client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(self.user)}')

        unit = Unit.objects.create(name='台', created_by=self.admin)
        category = Category.objects.create(name='专用设备', unit=unit, created_by=self.admin)
        variety = Variety.objects.create(name='侦测设备', category=category, created_by=self.admin)
        self.goods = Goods.objects.create(
            variety=variety, name='便携式侦测仪', code='SPEC-001',
            quantity=Decimal('10'), warning_threshold=Decimal('2'),
        )
        self.base_time = timezone.now()

    def create_application(self, case_name, level, requested, minimum,
                           committed_offset_hours, applicant=None, created_offset_minutes=0):
        app = EquipmentApplication.objects.create(
            applicant=applicant or self.user,
            goods=self.goods,
            case_name=case_name,
            case_level=level,
            requested_qty=Decimal(str(requested)),
            minimum_qty=Decimal(str(minimum)),
            committed_at=self.base_time + timedelta(hours=committed_offset_hours),
        )
        if created_offset_minutes:
            # 稳定排序的兜底键之一：申请时间
            EquipmentApplication.objects.filter(pk=app.pk).update(
                created_at=app.created_at + timedelta(minutes=created_offset_minutes)
            )
            app.refresh_from_db()
        return app


class AllocationAlgorithmTest(AllocationFixture):
    def item_map(self, plan):
        return {item.application.case_name: item for item in plan.items.all()}

    def test_level_beats_commit_time_and_request_order(self):
        # 一般案件承诺更早、申请更早，但重大案件必须排在前面
        self.create_application('一般案件A', 'general', 10, 0, committed_offset_hours=1)
        self.create_application('重大案件B', 'major', 10, 0, committed_offset_hours=48)
        plan = build_plan(self.goods.id, self.admin)

        items = list(plan.items.order_by('rank'))
        self.assertEqual([i.application.case_name for i in items], ['重大案件B', '一般案件A'])
        results = self.item_map(plan)
        self.assertEqual(results['重大案件B'].allocated_qty, Decimal('10'))
        self.assertEqual(results['重大案件B'].result, 'full')
        self.assertEqual(results['一般案件A'].allocated_qty, Decimal('0'))
        self.assertEqual(results['一般案件A'].result, 'none')
        # 每条结果都有可解释说明
        self.assertIn('顺位1', results['重大案件B'].reason)
        self.assertIn('顺位2', results['一般案件A'].reason)

    def test_minimum_guarantee_allocated_before_topping_up_anyone(self):
        self.create_application('重大案', 'major', 8, 8, committed_offset_hours=10)
        self.create_application('重要案', 'important', 2, 2, committed_offset_hours=10)
        self.create_application('一般案', 'general', 10, 3, committed_offset_hours=1)
        plan = build_plan(self.goods.id, self.admin)
        results = self.item_map(plan)

        # 保障轮依次 8 + 2 用尽库存；一般案件虽承诺最早，但等级最低且未获保障
        self.assertEqual(results['重大案'].allocated_qty, Decimal('8'))
        self.assertEqual(results['重要案'].allocated_qty, Decimal('2'))
        self.assertEqual(results['一般案'].allocated_qty, Decimal('0'))
        self.assertEqual(results['一般案'].result, 'none')
        self.assertIn('保障轮终止', results['一般案'].reason)

    def test_partial_satisfaction_when_stock_runs_out_mid_guarantee(self):
        self.goods.quantity = Decimal('5')
        self.goods.save()
        self.create_application('重大案', 'major', 10, 8, committed_offset_hours=5)
        self.create_application('重要案', 'important', 2, 2, committed_offset_hours=1)
        plan = build_plan(self.goods.id, self.admin)
        results = self.item_map(plan)

        self.assertEqual(results['重大案'].result, 'partial')
        self.assertEqual(results['重大案'].allocated_qty, Decimal('5'))
        self.assertIn('部分发放5/8', results['重大案'].reason)
        self.assertEqual(results['重要案'].result, 'none')
        self.assertEqual(results['重要案'].allocated_qty, Decimal('0'))

    def test_topup_round_fills_by_same_rank_order(self):
        # 保障后仍有剩余：按同一顺位向申请量补足
        self.create_application('重大案', 'major', 6, 2, committed_offset_hours=5)
        self.create_application('重要案', 'important', 6, 2, committed_offset_hours=1)
        plan = build_plan(self.goods.id, self.admin)
        results = self.item_map(plan)

        # 保障轮各2（用4），补足轮重大案先+4至6（用掉余6），重要案再+2=4
        self.assertEqual(results['重大案'].allocated_qty, Decimal('6'))
        self.assertEqual(results['重大案'].result, 'full')
        self.assertEqual(results['重要案'].allocated_qty, Decimal('4'))
        self.assertEqual(results['重要案'].result, 'partial')

    def test_same_level_commit_time_then_created_time_are_stable(self):
        first = self.create_application('同等级-承诺早', 'major', 10, 0,
                                        committed_offset_hours=2, created_offset_minutes=10)
        second = self.create_application('同等级-承诺晚', 'major', 10, 0,
                                         committed_offset_hours=20, created_offset_minutes=0)
        plan = build_plan(self.goods.id, self.admin)
        items = list(plan.items.order_by('rank'))
        self.assertEqual([i.application.case_name for i in items],
                         ['同等级-承诺早', '同等级-承诺晚'])

        # 承诺时间相同：申请时间早者优先（first 创建时间反而更晚）
        AllocationPlan.objects.all().delete()
        first.committed_at = second.committed_at
        first.save(update_fields=['committed_at'])
        plan = build_plan(self.goods.id, self.admin)
        items = list(plan.items.order_by('rank'))
        self.assertEqual(items[0].application.case_name, '同等级-承诺晚')

    def test_snapshot_is_explainable_and_versioned(self):
        self.create_application('重大案', 'major', 4, 1, committed_offset_hours=2)
        plan = build_plan(self.goods.id, self.admin)
        self.assertEqual(plan.snapshot['rule_version'], RULE_VERSION)
        self.assertEqual(Decimal(plan.snapshot['available_quantity']), Decimal('10'))
        self.assertEqual(plan.snapshot['applications'][0]['case_level'], 'major')
        self.assertIn('承诺时间', plan.snapshot['order_rule'])

    def test_compute_rows_is_pure_and_deterministic(self):
        self.create_application('甲', 'major', 5, 1, committed_offset_hours=2)
        self.create_application('乙', 'major', 5, 1, committed_offset_hours=3)
        apps = list(EquipmentApplication.objects.order_by('id'))
        rows_a = _compute_rows(apps, Decimal('10'))
        rows_b = _compute_rows(apps, Decimal('10'))
        self.assertEqual(
            [(r['rank'], r['allocated_qty'], r['result']) for r in rows_a],
            [(r['rank'], r['allocated_qty'], r['result']) for r in rows_b],
        )


class PreviewAndConfirmTest(AllocationFixture):
    def test_build_supersedes_old_draft_but_never_confirmed(self):
        self.create_application('案件1', 'major', 3, 1, committed_offset_hours=2)
        first = build_plan(self.goods.id, self.admin)
        second = build_plan(self.goods.id, self.admin)

        first.refresh_from_db()
        self.assertEqual(first.status, 'superseded')
        self.assertEqual(second.status, 'draft')
        # 已确认方案不允许再改状态
        second = confirm_plan(second.id, self.admin)
        third = build_plan(self.goods.id, self.admin)
        second.refresh_from_db()
        self.assertEqual(second.status, 'confirmed')
        self.assertEqual(third.status, 'draft')

    def test_confirm_writes_occupations_and_deducts_stock_atomically(self):
        app1 = self.create_application('重大案', 'major', 6, 2, committed_offset_hours=2)
        app2 = self.create_application('重要案', 'important', 6, 2, committed_offset_hours=1)
        app3 = self.create_application('一般案', 'general', 2, 0, committed_offset_hours=1)
        plan = build_plan(self.goods.id, self.admin)
        confirm_plan(plan.id, self.admin)

        app1.refresh_from_db(); app2.refresh_from_db(); app3.refresh_from_db()
        self.goods.refresh_from_db()
        # 保障2+2 → 余6；补足重大+4；重要+2；一般0（保持待分配，不占用）
        self.assertEqual(app1.allocated_qty, Decimal('6'))
        self.assertEqual(app1.status, 'allocated')
        self.assertEqual(app2.allocated_qty, Decimal('4'))
        self.assertEqual(app2.status, 'allocated')
        self.assertEqual(app3.allocated_qty, Decimal('0'))
        self.assertEqual(app3.status, 'pending')
        self.assertEqual(self.goods.quantity, Decimal('0'))  # 10 - 10

    def test_recalculate_uses_remaining_stock_and_keeps_confirmed_intact(self):
        app1 = self.create_application('重大案', 'major', 6, 6, committed_offset_hours=2)
        app2 = self.create_application('重要案', 'important', 4, 4, committed_offset_hours=1)
        app3 = self.create_application('一般案', 'general', 5, 5, committed_offset_hours=0)
        plan = build_plan(self.goods.id, self.admin)
        results = {i.application.case_name: i for i in plan.items.all()}
        # 保障轮：6 + 4 用尽10，一般案0且保障轮终止，保持待分配
        self.assertEqual(results['一般案'].allocated_qty, Decimal('0'))
        confirm_plan(plan.id, self.admin)
        app1.refresh_from_db(); app2.refresh_from_db(); app3.refresh_from_db()
        self.assertEqual(app1.status, 'allocated')
        self.assertEqual(app2.status, 'allocated')
        self.assertEqual(app3.status, 'pending')
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal('0'))

        # 设备回收入库后重新预览：只有待分配的一般案进入，已确认占用原封不动
        self.goods.quantity = Decimal('5')
        self.goods.save(update_fields=['quantity'])
        new_plan = build_plan(self.goods.id, self.admin)
        items = {i.application.case_name: i for i in new_plan.items.all()}
        self.assertEqual(set(items), {'一般案'})
        self.assertEqual(items['一般案'].allocated_qty, Decimal('5'))
        confirm_plan(new_plan.id, self.admin)

        app1.refresh_from_db(); app2.refresh_from_db(); app3.refresh_from_db()
        self.assertEqual(app1.allocated_qty, Decimal('6'))  # 已确认未被重算改动
        self.assertEqual(app2.allocated_qty, Decimal('4'))
        self.assertEqual(app3.allocated_qty, Decimal('5'))
        self.assertEqual(app3.status, 'allocated')
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal('0'))  # 5 - 5

    def test_confirm_rejects_when_live_stock_shrunk(self):
        self.create_application('重大案', 'major', 8, 8, committed_offset_hours=2)
        plan = build_plan(self.goods.id, self.admin)
        # 预览后库存被其他途径调减
        self.goods.quantity = Decimal('3')
        self.goods.save(update_fields=['quantity'])
        with self.assertRaises(AllocationError):
            confirm_plan(plan.id, self.admin)
        plan.refresh_from_db()
        self.assertEqual(plan.status, 'draft')  # 失败不留副作用

    def test_confirm_rejects_superseded_and_double_confirm(self):
        self.create_application('重大案', 'major', 2, 1, committed_offset_hours=2)
        plan = build_plan(self.goods.id, self.admin)
        build_plan(self.goods.id, self.admin)  # 作废前草稿
        with self.assertRaises(AllocationError):
            confirm_plan(plan.id, self.admin)

        fresh = AllocationPlan.objects.get(status='draft')
        confirm_plan(fresh.id, self.admin)
        with self.assertRaises(AllocationError):
            confirm_plan(fresh.id, self.admin)


class WithdrawAndAdjustTest(AllocationFixture):
    def test_withdraw_invalidates_draft(self):
        app = self.create_application('待撤案', 'major', 4, 1, committed_offset_hours=2)
        self.create_application('另案', 'important', 4, 1, committed_offset_hours=3)
        plan = build_plan(self.goods.id, self.admin)

        withdraw_application(app.id, self.user)
        app.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(app.status, 'withdrawn')
        self.assertEqual(plan.status, 'superseded')
        self.assertIn('撤回', plan.invalid_reason)
        with self.assertRaises(AllocationError):
            confirm_plan(plan.id, self.admin)

    def test_withdraw_allocated_rejected_and_release_returns_stock(self):
        app = self.create_application('已占用案', 'major', 4, 4, committed_offset_hours=2)
        plan = build_plan(self.goods.id, self.admin)
        confirm_plan(plan.id, self.admin)
        with self.assertRaises(AllocationError):
            withdraw_application(app.id, self.user)

        release_application(app.id, self.admin)
        app.refresh_from_db()
        self.goods.refresh_from_db()
        self.assertEqual(app.status, 'released')
        self.assertEqual(self.goods.quantity, Decimal('10'))
        with self.assertRaises(AllocationError):
            release_application(app.id, self.admin)

    def test_manual_adjust_rules(self):
        target = self.create_application('调整目标', 'major', 6, 2, committed_offset_hours=2)
        self.create_application('另一案', 'important', 6, 2, committed_offset_hours=3)
        plan = build_plan(self.goods.id, self.admin)
        item = AllocationItem.objects.get(application=target)

        with self.assertRaises(AllocationError):
            adjust_item(plan.id, item.id, self.admin, Decimal('7'), '超申请量')
        with self.assertRaises(AllocationError):
            adjust_item(plan.id, item.id, self.admin, Decimal('3'), '')
        # 低于最低保障量必须显式确认
        with self.assertRaises(AllocationError):
            adjust_item(plan.id, item.id, self.admin, Decimal('1'), '特殊情况')
        adjusted = adjust_item(
            plan.id, item.id, self.admin, Decimal('1'), '特殊情况',
            confirm_below_minimum=True,
        )
        self.assertEqual(adjusted.allocated_qty, Decimal('1'))
        self.assertTrue(adjusted.is_manual_adjusted)
        self.assertEqual(adjusted.adjusted_by, self.admin)
        self.assertIn('低于最低保障量', adjusted.reason)

    def test_manual_adjust_cannot_exceed_stock_or_recalculate_others(self):
        target = self.create_application('调整目标', 'major', 6, 2, committed_offset_hours=2)
        other = self.create_application('另一案', 'important', 6, 2, committed_offset_hours=3)
        plan = build_plan(self.goods.id, self.admin)
        target_item = AllocationItem.objects.get(application=target)
        other_before = AllocationItem.objects.get(application=other)

        # 另一案算法结果为4；把目标调到8 → 合计12 > 可用10，拒绝
        with self.assertRaises(AllocationError):
            adjust_item(plan.id, target_item.id, self.admin, Decimal('8'), '临时需要')
        # 合法调整不级联改动其他明细
        adjust_item(plan.id, target_item.id, self.admin, Decimal('5'), '临时需要')
        other_after = AllocationItem.objects.get(application=other)
        self.assertEqual(other_after.allocated_qty, other_before.allocated_qty)

    def test_adjust_rejected_after_confirm(self):
        app = self.create_application('案件', 'major', 4, 1, committed_offset_hours=2)
        plan = build_plan(self.goods.id, self.admin)
        confirm_plan(plan.id, self.admin)
        item = AllocationItem.objects.get(application=app)
        with self.assertRaises(AllocationError):
            adjust_item(plan.id, item.id, self.admin, Decimal('2'), '已确认不可改')


class AllocationAPITest(AllocationFixture):
    def post_application(self, client, case_name, level, requested, minimum, hours):
        return client.post('/api/equipment-applications/', {
            'goods': self.goods.id,
            'case_name': case_name,
            'case_level': level,
            'requested_qty': str(requested),
            'minimum_qty': str(minimum),
            'committed_at': (self.base_time + timedelta(hours=hours)).isoformat(),
        }, format='json')

    def test_create_application_validation(self):
        ok = self.post_application(self.user_client, '案件A', 'major', 5, 6, 2)
        self.assertEqual(ok.status_code, 400)  # 保障量超过申请量
        ok = self.post_application(self.user_client, '案件A', 'major', 5, 1, 2)
        self.assertEqual(ok.status_code, 200)
        bad_level = self.post_application(self.user_client, '案件B', 'supreme', 5, 1, 2)
        self.assertEqual(bad_level.status_code, 400)

    def test_preview_confirm_end_to_end(self):
        self.post_application(self.user_client, '重大案', 'major', 8, 4, 5)
        self.post_application(self.user_client, '重要案', 'important', 8, 4, 1)

        preview = self.admin_client.post(
            '/api/allocation-plans/preview/', {'goods': self.goods.id}, format='json'
        )
        self.assertEqual(preview.status_code, 200)
        plan_id = preview.json()['data']['id']
        self.assertEqual(preview.json()['data']['status'], 'draft')
        self.assertEqual(len(preview.json()['data']['items']), 2)

        # 普通用户不能确认、不能调整
        forbidden = self.user_client.post(f'/api/allocation-plans/{plan_id}/confirm/', {}, format='json')
        self.assertEqual(forbidden.status_code, 403)

        confirmed = self.admin_client.post(f'/api/allocation-plans/{plan_id}/confirm/', {}, format='json')
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['data']['status'], 'confirmed')
        self.goods.refresh_from_db()
        # 保障4+4，余2补足顺位1的重大案 → 6/4，库存扣10
        self.assertEqual(self.goods.quantity, Decimal('0'))

        # 已确认方案不能重复确认
        again = self.admin_client.post(f'/api/allocation-plans/{plan_id}/confirm/', {}, format='json')
        self.assertEqual(again.status_code, 400)

    def test_adjust_api_requires_reason_and_persists(self):
        self.post_application(self.user_client, '重大案', 'major', 8, 4, 5)
        plan_id = self.admin_client.post(
            '/api/allocation-plans/preview/', {'goods': self.goods.id}, format='json'
        ).json()['data']['id']
        item_id = AllocationItem.objects.get(plan_id=plan_id).id

        no_reason = self.admin_client.post(
            f'/api/allocation-plans/{plan_id}/items/{item_id}/adjust/',
            {'allocated_qty': '5'}, format='json',
        )
        self.assertEqual(no_reason.status_code, 400)

        # 3 低于最低保障量4，需显式确认
        below = self.admin_client.post(
            f'/api/allocation-plans/{plan_id}/items/{item_id}/adjust/',
            {'allocated_qty': '3', 'adjust_reason': '现场统筹'}, format='json',
        )
        self.assertEqual(below.status_code, 400)

        ok = self.admin_client.post(
            f'/api/allocation-plans/{plan_id}/items/{item_id}/adjust/',
            {'allocated_qty': '3', 'adjust_reason': '现场统筹',
             'confirm_below_minimum': True}, format='json',
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()['data']['allocated_qty'], '3.00')
        self.assertTrue(ok.json()['data']['is_manual_adjusted'])
        self.assertEqual(ok.json()['data']['adjust_reason'], '现场统筹')

    def test_withdraw_permissions_and_draft_invalidation(self):
        resp = self.post_application(self.user_client, '我的案件', 'major', 4, 1, 2)
        app_id = resp.json()['data']['id']
        other_user = User.objects.create_user('other-user', 'testpass123', role='user')
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f'Bearer {generate_token(other_user)}')

        denied = other_client.post(f'/api/equipment-applications/{app_id}/withdraw/', {}, format='json')
        self.assertEqual(denied.status_code, 403)

        plan_id = self.admin_client.post(
            '/api/allocation-plans/preview/', {'goods': self.goods.id}, format='json'
        ).json()['data']['id']
        withdrawn = self.user_client.post(
            f'/api/equipment-applications/{app_id}/withdraw/', {}, format='json'
        )
        self.assertEqual(withdrawn.status_code, 200)
        self.assertEqual(AllocationPlan.objects.get(pk=plan_id).status, 'superseded')

    def test_release_requires_admin(self):
        resp = self.post_application(self.user_client, '案件', 'major', 4, 4, 2)
        app_id = resp.json()['data']['id']
        plan_id = self.admin_client.post(
            '/api/allocation-plans/preview/', {'goods': self.goods.id}, format='json'
        ).json()['data']['id']
        self.admin_client.post(f'/api/allocation-plans/{plan_id}/confirm/', {}, format='json')

        denied = self.user_client.post(f'/api/equipment-applications/{app_id}/release/', {}, format='json')
        self.assertEqual(denied.status_code, 403)
        ok = self.admin_client.post(f'/api/equipment-applications/{app_id}/release/', {}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal('10'))
