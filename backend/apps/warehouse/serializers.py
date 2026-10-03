"""
仓库管理序列化器
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval,
    EquipmentApplication, AllocationBatch, AllocationItem,
    AllocationOccupancy, AllocationAdjustmentLog,
)


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    is_warning = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'warning_threshold', 'location',
            'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    
    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockOutSerializer(serializers.ModelSerializer):
    """出库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity', 'status', 'status_display',
            'stock_out_time', 'remark', 'created_at'
        ]


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器"""
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id', 'stock_out', 'approver', 'approver_name',
            'status', 'status_display', 'remark', 'created_at', 'updated_at'
        ]


# ==================== 专用设备分配 ====================

class EquipmentApplicationSerializer(serializers.ModelSerializer):
    """专用设备申请序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    case_level_display = serializers.CharField(source='get_case_level_display', read_only=True)
    occupied_quantity = serializers.SerializerMethodField()

    class Meta:
        model = EquipmentApplication
        fields = [
            'id', 'goods', 'goods_name', 'case_name', 'case_level', 'case_level_display',
            'applicant', 'applicant_name', 'apply_time', 'promised_time',
            'quantity', 'minimum_guarantee', 'status', 'status_display',
            'occupied_quantity', 'remark', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'applicant', 'status', 'created_at', 'updated_at']

    def get_occupied_quantity(self, obj):
        occupancy = getattr(obj, 'occupancy', None)
        return str(occupancy.quantity) if occupancy else None


class EquipmentApplicationCreateSerializer(serializers.Serializer):
    """专用设备申请创建序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择专用设备'})
    case_name = serializers.CharField(min_length=1, max_length=200, required=True, error_messages={
        'required': '请输入案件名称', 'blank': '案件名称不能为空',
    })
    case_level = serializers.ChoiceField(
        choices=['major', 'important', 'normal'], required=True,
        error_messages={'required': '请选择案件等级', 'invalid_choice': '案件等级不合法'}
    )
    apply_time = serializers.DateTimeField(required=True, error_messages={'required': '请填写申请时间'})
    promised_time = serializers.DateTimeField(required=True, error_messages={'required': '请填写承诺归还时间'})
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
        required=True, error_messages={'required': '请填写申请数量', 'min_value': '申请数量须大于0'}
    )
    minimum_guarantee = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0'),
        required=True, error_messages={'required': '请填写最低保障量', 'min_value': '最低保障量不能为负'}
    )
    remark = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('专用设备不存在')
        return value

    def validate(self, data):
        if data['promised_time'] <= data['apply_time']:
            raise serializers.ValidationError('承诺归还时间必须晚于申请时间')
        if data['minimum_guarantee'] > data['quantity']:
            raise serializers.ValidationError('最低保障量不能超过申请数量')
        return data


class AllocationItemSerializer(serializers.ModelSerializer):
    """分配明细序列化器"""
    application = EquipmentApplicationSerializer(read_only=True)
    application_id = serializers.IntegerField(source='application.id', read_only=True)
    result_display = serializers.CharField(source='get_result_display', read_only=True)
    adjusted_by_name = serializers.CharField(source='adjusted_by.username', read_only=True)

    class Meta:
        model = AllocationItem
        fields = [
            'id', 'application_id', 'application', 'rank',
            'proposed_quantity', 'final_quantity', 'result', 'result_display',
            'reason', 'manual_overridden', 'adjusted_by', 'adjusted_by_name',
        ]


class AllocationBatchSerializer(serializers.ModelSerializer):
    """分配批次（预览/确认）序列化器"""
    items = AllocationItemSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.CharField(source='get_source_display', read_only=True)
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    confirmed_by_name = serializers.CharField(source='confirmed_by.username', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    final_total = serializers.SerializerMethodField()

    class Meta:
        model = AllocationBatch
        fields = [
            'id', 'goods', 'goods_name', 'status', 'status_display',
            'source', 'source_display',
            'available_snapshot', 'occupied_snapshot', 'final_total',
            'confirmed_at', 'confirmed_by', 'confirmed_by_name',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'items',
        ]

    def get_final_total(self, obj):
        items = obj.items.all()
        total = sum((item.final_quantity for item in items), Decimal('0'))
        return str(total)


class AllocationAdjustSerializer(serializers.Serializer):
    """预览人工调整入参：[{application: id, quantity: 数量}]"""
    application = serializers.IntegerField(required=True)
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0'), required=True
    )


class OccupancyAdjustSerializer(serializers.Serializer):
    """确认后占用调整入参"""
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0'), required=True,
        error_messages={'required': '请填写调整后数量'}
    )


class AllocationAdjustmentLogSerializer(serializers.ModelSerializer):
    """分配操作日志序列化器"""
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    case_name = serializers.CharField(source='application.case_name', read_only=True)

    class Meta:
        model = AllocationAdjustmentLog
        fields = [
            'id', 'batch', 'application', 'case_name', 'operator_name',
            'action', 'action_display',
            'before_quantity', 'after_quantity', 'detail', 'created_at',
        ]
