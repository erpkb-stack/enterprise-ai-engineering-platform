# Checkout Service Architecture (DEMO DATA)

The checkout service is a Python FastAPI application owned by the payments team.
It runs six replicas across two availability zones behind an internal load balancer.
Checkout calls the payment gateway synchronously with a 2 second timeout and a circuit breaker that opens after 5 consecutive failures.
Order events are published to a durable queue so that fulfilment and email notifications are processed asynchronously.

## Data stores
Checkout uses PostgreSQL as the system of record with one synchronous standby replica.
Redis is used as a read-through cache for product prices with a 60 second TTL.

## Known risks
The fraud scoring dependency has no fallback, so fraud service outages block checkout completely.
Configuration rollouts are applied to all replicas at once, which caused the November 2026 latency regression.
