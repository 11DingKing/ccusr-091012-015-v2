"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    DashboardView, GoodsListView, StockInListView, StockOutListView,
    WarningListView, ApprovalListView,
    EquipmentApplicationListView, EquipmentApplicationWithdrawView,
    EquipmentApplicationReleaseView,
    AllocationPlanPreviewView, AllocationPlanListView, AllocationPlanDetailView,
    AllocationPlanConfirmView, AllocationItemAdjustView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    
    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),
    
    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),
    
    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),
    
    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),
    
    # 入库管理
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),
    
    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),
    
    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),
    
    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),

    # 专用设备占用申请
    path('equipment-applications/', EquipmentApplicationListView.as_view(),
         name='equipment-application-list'),
    path('equipment-applications/<int:pk>/withdraw/',
         EquipmentApplicationWithdrawView.as_view(), name='equipment-application-withdraw'),
    path('equipment-applications/<int:pk>/release/',
         EquipmentApplicationReleaseView.as_view(), name='equipment-application-release'),

    # 分配方案：预览 → 人工调整 → 确认
    path('allocation-plans/preview/', AllocationPlanPreviewView.as_view(),
         name='allocation-plan-preview'),
    path('allocation-plans/', AllocationPlanListView.as_view(), name='allocation-plan-list'),
    path('allocation-plans/<int:pk>/', AllocationPlanDetailView.as_view(),
         name='allocation-plan-detail'),
    path('allocation-plans/<int:pk>/confirm/', AllocationPlanConfirmView.as_view(),
         name='allocation-plan-confirm'),
    path('allocation-plans/<int:plan_pk>/items/<int:item_pk>/adjust/',
         AllocationItemAdjustView.as_view(), name='allocation-item-adjust'),
]
