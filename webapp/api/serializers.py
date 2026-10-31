from rest_framework import serializers
from .models import DimCompany, FactProfitLoss

class FactProfitLossSerializer(serializers.ModelSerializer):
    class Meta:
        model = FactProfitLoss
        fields = '__all__'

class CompanySerializer(serializers.ModelSerializer):
    # This nests the P&L data inside the company endpoint
    profit_and_loss = FactProfitLossSerializer(source='factprofitloss_set', many=True, read_only=True)
    
    class Meta:
        model = DimCompany
        fields = [
            'symbol', 
            'company_name', 
            'sector', 
            'sub_sector', 
            'website', 
            'profit_and_loss'
        ]