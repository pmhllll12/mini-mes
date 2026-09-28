"""
Prometheus 메트릭

- mes_equipment_oee / mes_equipment_availability / mes_equipment_quality_rate:
  설비별 최근 OEE_WINDOW_HOURS 동안의 OEE/가동률/양품률 (스크레이프 시점에 계산)
- mes_defect_qty: 설비별·불량유형별 최근 DEFECT_WINDOW_HOURS 동안의 불량 수량 (스크레이프 시점에 계산)
- mes_http_requests_total / mes_http_request_duration_seconds: API 요청 수·지연시간 (미들웨어에서 직접 기록)
"""
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Histogram, REGISTRY
from prometheus_client.core import GaugeMetricFamily

import models
from database import SessionLocal
from oee import calculate_oee
from quality import calculate_defect_summary

OEE_WINDOW_HOURS = 1
DEFECT_WINDOW_HOURS = 24

HTTP_REQUEST_COUNT = Counter(
    "mes_http_requests_total", "Total HTTP requests", ["method", "path", "status_code"]
)
HTTP_REQUEST_LATENCY = Histogram(
    "mes_http_request_duration_seconds", "HTTP request latency (seconds)", ["method", "path"]
)


class MesMetricsCollector:
    """스크레이프 시점에 DB를 조회해 설비별 OEE/가동률/양품률/불량 집계를 계산하는 커스텀 컬렉터."""

    def collect(self):
        db = SessionLocal()
        try:
            end = datetime.now(timezone.utc)
            oee_start = end - timedelta(hours=OEE_WINDOW_HOURS)

            oee_gauge = GaugeMetricFamily(
                "mes_equipment_oee", f"OEE (최근 {OEE_WINDOW_HOURS}시간)", labels=["equipment_id"]
            )
            availability_gauge = GaugeMetricFamily(
                "mes_equipment_availability", f"가동률 (최근 {OEE_WINDOW_HOURS}시간)", labels=["equipment_id"]
            )
            quality_gauge = GaugeMetricFamily(
                "mes_equipment_quality_rate", f"양품률 (최근 {OEE_WINDOW_HOURS}시간)", labels=["equipment_id"]
            )

            equipment_ids = [row.equipment_id for row in db.query(models.Equipment.equipment_id).all()]
            for eq_id in equipment_ids:
                result = calculate_oee(db, eq_id, oee_start, end)
                oee_gauge.add_metric([eq_id], result["oee"])
                availability_gauge.add_metric([eq_id], result["availability"])
                quality_gauge.add_metric([eq_id], result["quality_rate"])

            yield oee_gauge
            yield availability_gauge
            yield quality_gauge

            defect_gauge = GaugeMetricFamily(
                "mes_defect_qty",
                f"불량 수량 (최근 {DEFECT_WINDOW_HOURS}시간)",
                labels=["equipment_id", "defect_type"],
            )
            defect_start = end - timedelta(hours=DEFECT_WINDOW_HOURS)
            for row in calculate_defect_summary(db, None, defect_start, end):
                defect_gauge.add_metric([row["equipment_id"], row["defect_type"]], row["total_qty_defect"])
            yield defect_gauge
        finally:
            db.close()


REGISTRY.register(MesMetricsCollector())
