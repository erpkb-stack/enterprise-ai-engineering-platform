# Incident Response Policy (DEMO DATA)

## Severity levels
Sev1 incidents are full outages of a customer-facing service and require the on-call incident commander to be paged within 5 minutes.
Sev2 incidents are significant degradations, such as checkout p95 latency above 800 milliseconds, and require acknowledgement within 15 minutes.
Sev3 incidents are minor issues with a workaround and are handled during business hours.

## Response process
The incident commander opens a dedicated incident channel and assigns a communications lead.
Every Sev1 and Sev2 incident requires a blameless postmortem within 5 business days.
Rollback is the preferred first mitigation when an incident correlates with a recent deployment or configuration change.

## Error budgets
Services with a 99.9 percent availability SLO have a monthly error budget of 43 minutes.
When the error budget burn rate exceeds 2, feature releases for that service are frozen until the burn rate recovers.
