# Design decisions

1. **Indexes.** Unique employee-code and employee/date indexes enforce identity and one attendance record per day. Department/code supports employee lists and department lookups; joined-date supports monthly headcount. Date/code and status/date/code support attendance filters and monthly aggregations. Employee/punch-time finds the latest eligible punch-in. I rejected a standalone half-day index because no endpoint filters on it and it would add write cost.

2. **Punch-in race.** Both requests validate the employee and calculate the attendance day, then attempt an insert. MongoDB's unique employee/date index accepts one insert. The other raises DuplicateKeyError, which becomes 409; the winner returns 201.

3. **Ties.** MongoDB's $rank assigns competition ranks before the cutoff. Every row whose rank is at most limit is returned, including ties. Employee code orders tied rows deterministically.

4. **Headcount.** The summary starts from eligible employees and looks up their logs. Employees without logs remain in the pipeline and contribute one to headcount, with zero attendance totals.

5. **Growth.** At 100 times the data, I would measure execution statistics and introduce incrementally maintained daily aggregates, keeping original logs and correction history authoritative and updating summaries when corrections occur.
