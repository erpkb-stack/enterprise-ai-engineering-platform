# Async Python Patterns (DEMO DATA)

## Choosing between gather and a semaphore
Use asyncio.gather when you call a small, fixed number of APIs concurrently, and wrap each call in an asyncio.Semaphore when you call many APIs, such as 50, so that only a safe number of requests run at once.
Short example in async Python: sem = asyncio.Semaphore(10); async def call(url): async with sem: return await client.get(url, timeout=5); results = await asyncio.gather(*(call(u) for u in urls), return_exceptions=True).
Pass return_exceptions=True to asyncio.gather so one failed API call does not cancel the others, then handle each exception in the results list.

## Timeouts and cancellation
Every outbound call needs its own timeout, and asyncio.timeout or asyncio.wait_for keeps a slow API from holding a semaphore slot forever.
Use asyncio.TaskGroup when all tasks must succeed together, because it cancels the remaining tasks as soon as one of them fails.

## Keeping the event loop responsive
CPU-bound work such as hashing or parsing large files blocks the event loop, so move it to a thread or process pool with asyncio.to_thread or run_in_executor.
Never call blocking libraries such as requests or time.sleep inside an async function; use httpx.AsyncClient and asyncio.sleep instead.
Reuse one shared HTTP client with connection pooling instead of creating a new client for every request.
