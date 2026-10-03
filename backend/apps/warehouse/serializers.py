"""
仓库管理序列化器
"""
from decimal import Decimal

from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval,
    EquipmentApplication, AllocationPlan, AllocationItem,
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


# ==================== 专用设备占用申请与分配 ====================


class EquipmentApplicationSerializer(serializers.ModelSerializer):
    """专用设备占用申请只读序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    case_level_display = serializers.CharField(source='get_case_level_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = EquipmentApplication
        fields = [
            'id', 'goods', 'goods_name', 'applicant', 'applicant_name',
            'case_name', 'case_level', 'case_level_display',
            'requested_qty', 'minimum_qty', 'committed_at',
            'allocated_qty', 'status', 'status_display',
            'created_at', 'updated_at',
        ]


class EquipmentApplicationCreateSerializer(serializers.Serializer):
    """专用设备占用申请创建/更新序列化器"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择专用设备'})
    case_name = serializers.CharField(min_length=1, max_length=100, required=True, error_messages={
        'required': '请输入案件名称',
        'blank': '案件名称不能为空',
        'max_length': '案件名称最多100个字',
    })
    case_level = serializers.ChoiceField(
        choices=['major', 'important', 'general'], required=True,
        error_messages={'required': '请选择案件等级', 'invalid_choice': '案件等级无效'},
    )
    requested_qty = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0.01'), required=True,
        error_messages={'required': '请输入申请数量', 'min_value': '申请数量必须大于0'},
    )
    minimum_qty = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0'), required=False,
    )
    committed_at = serializers.DateTimeField(
        required=True, error_messages={'required': '请选择承诺时间', 'invalid': '承诺时间格式无效'},
    )

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('专用设备不存在或已停用')
        return value

    def validate(self, attrs):
        minimum = attrs.get('minimum_qty', Decimal('0')) or Decimal('0')
        requested = attrs['requested_qty']
        if minimum > requested:
            raise serializers.ValidationError({'minimum_qty': '最低保障量不能超过申请数量'})
        attrs['minimum_qty'] = minimum
        return attrs


class AllocationItemSerializer(serializers.ModelSerializer):
    """分配方案明细序列化器"""
    application_id = serializers.IntegerField(source='application.id', read_only=True)
    case_name = serializers.CharField(source='application.case_name', read_only=True)
    case_level = serializers.CharField(source='application.case_level', read_only=True)
    case_level_display = serializers.CharField(
        source='application.get_case_level_display', read_only=True
    )
    committed_at = serializers.DateTimeField(source='application.committed_at', read_only=True)
    result_display = serializers.CharField(source='get_result_display', read_only=True)
    adjusted_by_name = serializers.CharField(source='adjusted_by.username', read_only=True)

    class Meta:
        model = AllocationItem
        fields = [
            'id', 'application_id', 'case_name', 'case_level', 'case_level_display',
            'committed_at', 'rank', 'requested_qty', 'minimum_qty',
            'allocated_qty', 'result', 'result_display', 'reason',
            'is_manual_adjusted', 'adjusted_by_name', 'adjust_reason',
        ]


class AllocationPlanSerializer(serializers.ModelSerializer):
    """分配方案序列化器（详情内嵌明细）"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    confirmed_by_name = serializers.CharField(source='confirmed_by.username', read_only=True)
    items = AllocationItemSerializer(many=True, read_only=True)
    rule_version = serializers.SerializerMethodField()

    class Meta:
        model = AllocationPlan
        fields = [
            'id', 'goods', 'goods_name', 'status', 'status_display',
            'total_available', 'snapshot', 'rule_version',
            'created_by', 'created_by_name', 'confirmed_by', 'confirmed_by_name',
            'confirmed_at', 'created_at', 'updated_at', 'items',
        ]

    def get_rule_version(self, obj):
        return obj.snapshot.get('rule_version') if isinstance(obj.snapshot, dict) else None


class AllocationAdjustSerializer(serializers.Serializer):
    """人工调整明细入参"""
    allocated_qty = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal('0'), required=True,
        error_messages={'required': '请输入调整后的数量', 'min_value': '分配数量不能为负'},
    )
    adjust_reason = serializers.CharField(min_length=1, max_length=200, required=True, error_messages={
        'required': '请填写人工调整原因',
        'blank': '人工调整原因不能为空',
    })
    confirm_below_minimum = serializers.BooleanField(required=False, default=False)
