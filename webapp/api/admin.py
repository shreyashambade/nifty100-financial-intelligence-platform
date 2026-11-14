from django.contrib import admin
from .models import ChannelPartner, APIUsageLog, WebhookSubscription, WebhookEvent, FactAnomalyFlag

@admin.register(ChannelPartner)
class ChannelPartnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'tier', 'api_key', 'is_active', 'created_at')
    search_fields = ('name', 'email', 'api_key')
    list_filter = ('tier', 'is_active', 'created_at')
    readonly_fields = ('api_key', 'secret_key', 'created_at')

@admin.register(APIUsageLog)
class APIUsageLogAdmin(admin.ModelAdmin):
    list_display = ('method', 'endpoint', 'status_code', 'partner', 'response_ms', 'ip_address', 'created_at')
    search_fields = ('endpoint', 'ip_address', 'partner__name', 'partner__api_key')
    list_filter = ('method', 'status_code', 'created_at')
    readonly_fields = ('partner', 'endpoint', 'method', 'status_code', 'response_ms', 'ip_address', 'request_size', 'response_size', 'created_at')

@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('partner', 'event_type', 'target_url', 'is_active', 'created_at')
    search_fields = ('partner__name', 'partner__email', 'target_url')
    list_filter = ('event_type', 'is_active', 'created_at')

@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'subscription', 'status', 'status_code', 'retry_count', 'created_at', 'delivered_at')
    search_fields = ('subscription__target_url', 'subscription__partner__name', 'last_error')
    list_filter = ('status', 'subscription__event_type', 'created_at')
    readonly_fields = ('subscription', 'payload', 'status', 'retry_count', 'status_code', 'response_body', 'last_error', 'created_at', 'delivered_at')


@admin.register(FactAnomalyFlag)
class FactAnomalyFlagAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'year_label', 'metric_name', 'metric_value', 'z_score', 'severity', 'is_multivariate_anomaly', 'is_reviewed', 'flagged_at')
    search_fields = ('symbol__symbol', 'year_label', 'metric_name')
    list_filter = ('severity', 'is_reviewed', 'is_multivariate_anomaly', 'metric_name')
    readonly_fields = ('flagged_at',)
