"""
库房管理模型
"""
from decimal import Decimal

from django.db import models
from apps.authentication.models import User


class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']

    def __str__(self):
        return f"{self.category.name} - {self.name}"

    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()

    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)

    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class StockOut(models.Model):
    """出库记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
        ('completed', '已完成'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class Approval(models.Model):
    """审批记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approvals', verbose_name='出库记录'
    )
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approvals', verbose_name='审批人'
    )
    status = models.CharField('审批状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    remark = models.TextField('审批意见', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval'
        verbose_name = '审批记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.stock_out} - {self.get_status_display()}"


# ==================== 专用设备分配 ====================

# 案件等级 -> 排序权重（值越大优先级越高）
CASE_LEVEL_CHOICES = [
    ('major', '重大案件'),
    ('important', '重要案件'),
    ('normal', '一般案件'),
]
CASE_LEVEL_WEIGHTS = {
    'major': 3,
    'important': 2,
    'normal': 1,
}


class EquipmentApplication(models.Model):
    """专用设备占用申请（对应一起案件对某专用设备的申领）"""
    STATUS_DRAFT = 'draft'          # 待提交
    STATUS_WAITING = 'waiting'      # 等待分配
    STATUS_ALLOCATED = 'allocated'  # 已确认占用
    STATUS_RETURNED = 'returned'    # 已归还
    STATUS_WITHDRAWN = 'withdrawn'  # 申请撤回

    STATUS_CHOICES = [
        (STATUS_DRAFT, '待提交'),
        (STATUS_WAITING, '等待分配'),
        (STATUS_ALLOCATED, '已占用'),
        (STATUS_RETURNED, '已归还'),
        (STATUS_WITHDRAWN, '已撤回'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='equipment_applications', verbose_name='专用设备'
    )
    case_name = models.CharField('案件名称', max_length=200)
    case_level = models.CharField('案件等级', max_length=20, choices=CASE_LEVEL_CHOICES)
    applicant = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='equipment_applications', verbose_name='申请人'
    )
    apply_time = models.DateTimeField('申请时间')
    promised_time = models.DateTimeField('承诺归还时间')
    quantity = models.DecimalField('申请数量', max_digits=12, decimal_places=2)
    minimum_guarantee = models.DecimalField('最低保障量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_equipment_application'
        verbose_name = '专用设备申请'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['goods', 'status']),
        ]

    def __str__(self):
        return f"{self.case_name} - {self.goods.name} - {self.quantity}"

    @property
    def level_weight(self):
        """案件等级权重"""
        return CASE_LEVEL_WEIGHTS.get(self.case_level, 0)


class AllocationBatch(models.Model):
    """分配批次：一次资源竞争的计算/确认单元

    状态流转：draft（预览） -> confirmed（原子确认）/ expired（数据已变）。
    已确认批次是不可变的事实，任何重新计算只会生成新的 draft 批次，
    不会回写历史批次或已确认占用。
    """
    SOURCE_MANUAL = 'manual'
    SOURCE_RECALC = 'recalc'
    STATUS_DRAFT = 'draft'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_EXPIRED = 'expired'

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, '人工触发计算'),
        (SOURCE_RECALC, '重新计算'),
    ]
    STATUS_CHOICES = [
        (STATUS_DRAFT, '预览中'),
        (STATUS_CONFIRMED, '已确认'),
        (STATUS_EXPIRED, '已失效'),
    ]

    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='allocation_batches', verbose_name='专用设备'
    )
    status = models.CharField('批次状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    source = models.CharField('计算来源', max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    available_snapshot = models.DecimalField(
        '计算时可分配库存快照', max_digits=12, decimal_places=2
    )
    occupied_snapshot = models.DecimalField(
        '计算时已确认占用快照', max_digits=12, decimal_places=2, default=Decimal('0')
    )
    # 参与计算的申请及其关键属性指纹；确认时若不一致则拒绝（乐观并发）
    input_fingerprint = models.CharField('输入数据指纹', max_length=64, default='')
    confirmed_at = models.DateTimeField('确认时间', null=True, blank=True)
    confirmed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='confirmed_allocation_batches', verbose_name='确认人'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_allocation_batches', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_allocation_batch'
        verbose_name = '分配批次'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"批次#{self.pk}({self.goods.name}-{self.get_status_display()})"

    @property
    def is_editable(self):
        """仅预览中的批次允许人工调整/确认"""
        return self.status == self.STATUS_DRAFT


class AllocationItem(models.Model):
    """批次内每起案件的分配行

    proposed_quantity：算法给出的可解释建议量；
    final_quantity：实际生效量（初始等于建议量，人工调整后不同）。
    """
    RESULT_FULL = 'full'
    RESULT_PARTIAL = 'partial'
    RESULT_UNMET = 'unmet'
    RESULT_WITHDRAWN = 'withdrawn'

    RESULT_CHOICES = [
        (RESULT_FULL, '足额满足'),
        (RESULT_PARTIAL, '部分满足'),
        (RESULT_UNMET, '未满足'),
        (RESULT_WITHDRAWN, '已撤回'),
    ]

    batch = models.ForeignKey(
        AllocationBatch, on_delete=models.CASCADE,
        related_name='items', verbose_name='分配批次'
    )
    application = models.ForeignKey(
        EquipmentApplication, on_delete=models.PROTECT,
        related_name='allocation_items', verbose_name='设备申请'
    )
    rank = models.PositiveIntegerField('分配顺位')
    proposed_quantity = models.DecimalField(
        '建议分配量', max_digits=12, decimal_places=2
    )
    final_quantity = models.DecimalField(
        '最终分配量', max_digits=12, decimal_places=2
    )
    result = models.CharField('满足结果', max_length=20, choices=RESULT_CHOICES)
    reason = models.CharField('分配说明', max_length=200, default='')
    manual_overridden = models.BooleanField('是否人工调整', default=False)
    adjusted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='adjusted_allocation_items', verbose_name='调整人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_allocation_item'
        verbose_name = '分配明细'
        verbose_name_plural = verbose_name
        ordering = ['rank']
        unique_together = [('batch', 'application')]

    def __str__(self):
        return f"{self.application.case_name}:{self.final_quantity}"


class AllocationOccupancy(models.Model):
    """已确认的设备占用：批次确认时在同一事务内原子写入

    一个申请至多存在一条有效占用；撤回申请/归还设备后占用删除，
    但 AllocationAdjustmentLog 与 confirmed 批次保留全部历史。
    """
    application = models.OneToOneField(
        EquipmentApplication, on_delete=models.PROTECT,
        related_name='occupancy', verbose_name='设备申请'
    )
    batch = models.ForeignKey(
        AllocationBatch, on_delete=models.PROTECT,
        related_name='occupancies', verbose_name='确认批次'
    )
    goods = models.ForeignKey(
        Goods, on_delete=models.PROTECT,
        related_name='occupancies', verbose_name='专用设备'
    )
    quantity = models.DecimalField('占用数量', max_digits=12, decimal_places=2)
    created_at = models.DateTimeField('占用开始时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_allocation_occupancy'
        verbose_name = '已确认占用'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.application.case_name}:{self.quantity}"


class AllocationAdjustmentLog(models.Model):
    """分配全流程留痕：生成预览、人工调整、确认、撤回、归还、重新计算"""
    ACTION_CALCULATE = 'calculate'
    ACTION_MANUAL_ADJUST = 'manual_adjust'
    ACTION_CONFIRM = 'confirm'
    ACTION_WITHDRAW = 'withdraw'
    ACTION_RETURN = 'return'
    ACTION_RECALC = 'recalc'

    ACTION_CHOICES = [
        (ACTION_CALCULATE, '生成预览'),
        (ACTION_MANUAL_ADJUST, '人工调整'),
        (ACTION_CONFIRM, '确认写入'),
        (ACTION_WITHDRAW, '申请撤回'),
        (ACTION_RETURN, '设备归还'),
        (ACTION_RECALC, '重新计算'),
    ]

    batch = models.ForeignKey(
        AllocationBatch, on_delete=models.CASCADE, null=True, blank=True,
        related_name='logs', verbose_name='分配批次'
    )
    application = models.ForeignKey(
        EquipmentApplication, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='allocation_logs', verbose_name='设备申请'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='allocation_op_logs', verbose_name='操作人'
    )
    action = models.CharField('操作类型', max_length=20, choices=ACTION_CHOICES)
    before_quantity = models.DecimalField(
        '调整前数量', max_digits=12, decimal_places=2, null=True, blank=True
    )
    after_quantity = models.DecimalField(
        '调整后数量', max_digits=12, decimal_places=2, null=True, blank=True
    )
    detail = models.TextField('操作说明', blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        db_table = 'wh_allocation_log'
        verbose_name = '分配操作日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_action_display()}#{self.batch_id}"
