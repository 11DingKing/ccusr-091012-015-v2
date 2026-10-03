# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

## 专用设备占用分配

多起案件同时申请数量有限的专用设备时，按公开、稳定、可解释的规则生成分配
预览，经管理员确认后原子写入各申请占用。

**排序规则**：案件等级（重大 > 重要 > 一般）→ 承诺时间早 → 申请时间早 →
申请ID。排序键唯一确定，重新计算结果可复现。

**分配规则**：先按顺位发放各申请的最低保障量（库存不足以足额覆盖某申请时
按余量部分发放，保障轮随即终止）；剩余库存再按同一顺位向申请量补足。
结果分为足额分配 / 部分满足 / 未分配，每条明细附中文分配说明。

- 预览与确认分离：预览不改动库存；同一设备只保留一个活跃草稿，重新预览
  自动作废旧草稿；已确认方案永不被重算覆盖。
- 人工调整只改指定草稿明细（不得超过申请量、合计不得超过库存、必填原因、
  低于保障量需显式确认），不级联重算其他明细。
- 申请撤回会立即作废含该申请的草稿；已占用设备经管理员释放后退回库存。

| 方法 & 路径 | 权限 | 说明 |
| --- | --- | --- |
| `POST /api/equipment-applications/` | 登录用户 | 提交占用申请 |
| `GET /api/equipment-applications/` | 登录用户 | 申请列表（支持 goods/status/case_level 过滤） |
| `POST /api/equipment-applications/<id>/withdraw/` | 本人或管理员 | 撤回待分配申请 |
| `POST /api/equipment-applications/<id>/release/` | 管理员 | 释放占用，数量退回库存 |
| `POST /api/allocation-plans/preview/` | 登录用户 | 生成分配预览（草稿） |
| `GET /api/allocation-plans/` / `/<id>/` | 登录用户 | 方案列表 / 详情（含逐条说明与输入快照） |
| `POST /api/allocation-plans/<id>/items/<item_id>/adjust/` | 管理员 | 人工调整草稿明细 |
| `POST /api/allocation-plans/<id>/confirm/` | 管理员 | 确认方案，原子写入占用并扣减库存 |

规则变更须递增 `apps/warehouse/allocation.py` 中的 `RULE_VERSION`。
