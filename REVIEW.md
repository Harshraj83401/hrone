# Starter review

Locations refer to the original candidate-kit `app/main.py`.

| # | Where | What is wrong | How to notice it | Fix |
|---|---|---|---|---|
| 1 | MongoClient and punch-in | Datetimes use host-local time and naive MongoDB values. | Run the service outside IST; attendance dates and lateness shift. | Use aware UTC BSON dates and explicit IST calendar calculations. |
| 2 | compute_late_minutes | Floors before checking the grace boundary. | 09:40:01 for a 09:30 shift incorrectly returns 0 instead of 10. | Compare elapsed seconds with 600, then floor minutes. |
| 3 | compute_late_minutes | Anchors shift start to the punch calendar day. | A 02:00 overnight punch belongs to the previous shift day. | Resolve the attendance day first and anchor shift start there. |
| 4 | compute_work_hours | Python round uses half-even and binary floats. | 1.365 hours should become 1.37. | Use Decimal and ROUND_HALF_UP. |
| 5 | compute_overtime | Counts overtime below the 30-minute threshold. | Punch-out 18:35 for an 18:30 end returns 5 instead of 0. | Require at least 30 whole minutes. |
| 6 | compute_overtime | Overnight shift end remains on the start date. | 22:00–06:00 shift, out next day 06:40, inflates overtime. | Move overnight shift end to the next day. |
| 7 | Timestamp conversion | Does not truncate milliseconds before calculations and storage. | 09:40:00.900 must count as 09:40:00. | Truncate input instants to whole seconds. |
| 8 | health | Always reports ready even if MongoDB is unavailable. | Stop MongoDB and GET /health still returns 200. | Ping MongoDB; return 503 on failure. |
| 9 | EmployeeIn | Missing employee-code, length, email, shift and date validation. | Invalid code or equal shift times are accepted. | Match the contract's field bounds and calendar validation. |
| 10 | create_employee | Check-then-insert is race-prone. | Concurrent duplicate creates can both return 201. | Unique index and DuplicateKeyError handling. |
| 11 | create_employee | created_at uses naive host time and is returned as an ISO string. | Response is not integer epoch milliseconds. | Set aware UTC server time and serialize instants as milliseconds. |
| 12 | list_employees | page * page_size skips the first page. | page=1 omits the first 20 employees. | Use (page-1) * page_size. |
| 13 | list_employees | total ignores department filtering. | Filter one department; total reports every employee. | Count using the same query. |
| 14 | list_employees | No deterministic ordering. | Repeated pages depend on insertion/storage order. | Sort by emp_code ascending. |
| 15 | Both list endpoints | Pagination bounds are unchecked. | page=0 or page_size=101 succeeds. | Validate page >= 1 and page_size between 1 and 100. |
| 16 | punch_in | Unknown employee is dereferenced. | Unknown emp_code raises a server error. | Return 404 before calculations. |
| 17 | PunchInIn | Arbitrary statuses are accepted. | ABSENT can be created with a punch time. | Restrict to PRESENT, WFH and ON_DUTY. |
| 18 | PunchInIn | Timestamp coercion and range checks are missing. | Seconds, strings, floats and out-of-range values reach conversion. | Strict integer epoch milliseconds with contract bounds. |
| 19 | punch_in | Duplicate check is race-prone. | Parallel punches create multiple daily records. | Unique employee/date index and atomic insertion. |
| 20 | punch_in | Attendance day ignores overnight exception. | 02:00 punch is filed under the wrong date. | Resolve date in IST with the overnight rule. |
| 21 | punch_in and list_attendance | Exposes attendance id, which the contract excludes. | Responses contain id. | Remove MongoDB _id and return the natural key only. |
| 22 | All record responses | Datetimes, including nested history, serialize as ISO strings. | punch_in and history.changes values are not integers. | Recursively convert datetime values at the HTTP boundary. |
| 23 | list_attendance | Loads all matching records and sorts/paginates in Python. | Memory and latency grow at 100,000 records. | Indexed database sort, skip, limit and count. |
| 24 | list_attendance | Sort omits emp_code tie-breaker. | Same-day records have unstable page order. | Sort date descending, then emp_code ascending. |
| 25 | list_attendance | Dates, date-range order and status are not validated. | Reversed range or impossible calendar dates succeed. | Validate ISO dates, range ordering and status enum. |
| 26 | list_attendance | Legacy missing fields are not defaulted. | Seeded record without history/half_day violates response shape. | Apply empty history, false half_day and documented defaults. |
| 27 | Application startup | Required indexes are never created. | Duplicate races and collection scans occur. | Create named indexes idempotently in lifespan startup. |

The missing punch-out, correction, analytics and explain routes are unfinished scope, rather than defects in an existing implementation; all are now implemented. Punch-out uses an atomic snapshot condition; corrections atomically update computed fields and append history, returning 409 if their snapshot became stale.

Examined and retained: `load_dotenv()` already gives real environment variables priority. PRESENT as the punch-in default is correct. Initial null punch-out/work-hours, zero overtime, false half-day and empty history are correct. Inclusive date comparisons are correct once inputs are validated. Removing employee `_id` is correct. Counting lateness from shift start, rather than subtracting grace, is correct. HTTP 201 for successful employee creation and punch-in is correct.
