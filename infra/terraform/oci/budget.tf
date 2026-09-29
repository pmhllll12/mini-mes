# 비용 안전장치: 상시 무료 범위만 쓰므로 지출은 0이어야 한다. 조금이라도 과금되면 이메일로 알린다.
# 예산은 알림일 뿐 지출을 막지 않는다. 평가는 몇 시간 간격이라 과금 후 알림까지 시간 차가 있다.
resource "oci_budget_budget" "free_tier_guard" {
  compartment_id = var.tenancy_ocid # 예산은 루트 compartment에만 만들 수 있다
  display_name   = "mini-mes-free-tier-guard"
  description    = "mini-mes: 상시 무료 범위를 벗어난 과금 감지"
  amount         = var.budget_amount
  reset_period   = "MONTHLY"
  target_type    = "COMPARTMENT"
  targets        = [local.compartment_id]
}

# 실제 지출이 예산의 1%를 넘으면 (예산 1이면 0.01 - 통화와 무관하게 사실상 "과금 발생")
resource "oci_budget_alert_rule" "actual_spend" {
  count          = var.budget_alert_email != "" ? 1 : 0
  budget_id      = oci_budget_budget.free_tier_guard.id
  display_name   = "actual-spend-over-1pct"
  type           = "ACTUAL"
  threshold      = 1
  threshold_type = "PERCENTAGE"
  recipients     = var.budget_alert_email
  message        = "mini-mes Oracle Cloud에 과금이 발생했습니다. 상시 무료 범위를 벗어난 자원이 있는지 확인하세요."
}

# 월말 예상 지출이 예산을 넘으면
resource "oci_budget_alert_rule" "forecast_spend" {
  count          = var.budget_alert_email != "" ? 1 : 0
  budget_id      = oci_budget_budget.free_tier_guard.id
  display_name   = "forecast-over-budget"
  type           = "FORECAST"
  threshold      = 100
  threshold_type = "PERCENTAGE"
  recipients     = var.budget_alert_email
  message        = "mini-mes Oracle Cloud의 이번 달 예상 지출이 예산을 넘습니다."
}
