"""
cron_views.py

Internal API endpoint to trigger scheduled ML tasks synchronously,
bypassing the need for a background Celery worker.
Protected by a secure pre-shared secret key (CRON_SECRET_KEY).
"""

import hmac
import logging
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiResponse,
)
from drf_spectacular.types import OpenApiTypes

from .ml_scoring import (
    calculate_company_health_scores,
    detect_financial_anomalies,
)

logger = logging.getLogger(__name__)


class RunScheduledTasksView(APIView):
    """
    Secure internal API view to trigger ML health scoring and anomaly detection
    synchronously without requiring an active Celery worker.
    
    Authentication:
      Requires Authorization: Bearer <CRON_SECRET_KEY> or X-Cron-Secret: <CRON_SECRET_KEY>.
      Returns HTTP 403 Forbidden if the secret does not match settings.CRON_SECRET_KEY.
    """
    # Explicitly clear default HMAC / session authentication so the header check runs directly
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    @extend_schema(
        summary="Trigger Scheduled ML Tasks Synchronously",
        description=(
            "Synchronously executes ML Health Scoring and Z-Score Anomaly Detection pipelines, "
            "bypassing background Celery workers.\n\n"
            "**Authentication**: Provide `X-Cron-Secret` header or `Authorization: Bearer <token>` "
            "matching the server's `CRON_SECRET_KEY`."
        ),
        parameters=[
            OpenApiParameter(
                name='X-Cron-Secret',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=False,
                description='Pre-shared cron secret key matching settings.CRON_SECRET_KEY'
            ),
            OpenApiParameter(
                name='Authorization',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=False,
                description='Bearer <CRON_SECRET_KEY>'
            ),
        ],
        responses={
            200: OpenApiResponse(description="ML tasks executed successfully."),
            403: OpenApiResponse(description="Forbidden: Invalid or missing cron secret."),
            500: OpenApiResponse(description="Internal error during task execution."),
        },
        tags=["Internal Scheduled Tasks"]
    )
    def post(self, request, *args, **kwargs):
        expected_secret = getattr(settings, 'CRON_SECRET_KEY', 'dev-secret-token-123')

        # Check X-Cron-Secret or Authorization header
        header_secret = request.headers.get('X-Cron-Secret') or request.META.get('HTTP_X_CRON_SECRET')
        auth_header = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION')

        provided_key = None
        if header_secret:
            provided_key = str(header_secret).strip()
        elif auth_header:
            auth_str = str(auth_header).strip()
            if auth_str.lower().startswith('bearer '):
                provided_key = auth_str[7:].strip()
            else:
                provided_key = auth_str

        if not provided_key or not hmac.compare_digest(provided_key, expected_secret):
            logger.warning("Unauthorized attempt to access RunScheduledTasksView: invalid or missing cron secret.")
            return Response(
                {"detail": "Forbidden: Invalid or missing cron secret key."},
                status=status.HTTP_403_FORBIDDEN
            )

        logger.info("Authorized invocation of RunScheduledTasksView. Executing ML tasks synchronously...")

        try:
            # 1. Compute dynamic ML health scores synchronously
            scores_result = calculate_company_health_scores(dry_run=False)

            # 2. Compute statistical Z-score and multivariate anomalies synchronously
            anomalies_result = detect_financial_anomalies(threshold_sigma=3.0, dry_run=False)

            response_data = {
                "status": "success",
                "message": "Scheduled ML tasks executed successfully.",
                "tasks": {
                    "health_scoring": scores_result,
                    "anomaly_detection": anomalies_result,
                }
            }
            logger.info("Synchronous execution of scheduled ML tasks completed successfully.")
            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as exc:
            logger.error(f"Error during synchronous execution of scheduled ML tasks: {exc}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": f"Failed to execute scheduled ML tasks: {str(exc)}"
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
