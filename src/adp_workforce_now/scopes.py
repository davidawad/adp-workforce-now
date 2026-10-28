"""Application scopes (canonical URIs) for the read endpoints.

ADP scopes are not sent in the token request. They are added to the Consumer Application
Registry (CAR) for the subscription in API Central, and the access token carries whatever the
subscription is entitled to. A missing scope shows up as HTTP 403 ``insufficient_scope``.
"""

WORKERS_READ = (
    "/hr/workerInformationManagement/workerManagement/workerProfileManagement/worker.read"
)
TIME_OFF_BALANCE_READ = (
    "/time/timeLaborManagement/timeOffManagement/timeOffBalancesManagement/timeOffBalance.read"
)
TIME_OFF_REQUEST_READ = (
    "/time/timeLaborManagement/timeOffManagement/timeOffRequestManagement/timeOffRequest.read"
)
# Documented for ADP LINK / other ADP products; NOT verified for Workforce Now.
PAY_STATEMENT_READ = (
    "/payroll/payrollManagement/payStatementManagement/payStatementViewing/payStatement.read"
)
PAYROLL_OUTPUT_READ = (
    "/payroll/payrollManagement/payrollProcessingResults/payrollOutputManagement/payrollOutput.read"
)
