"""Employee Attendance & Analytics API, contract v2.0."""
import calendar
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from dotenv import load_dotenv
from bson.decimal128 import Decimal128
from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(override=False)
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
PRESENT = ["PRESENT", "WFH", "ON_DUTY"]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
Millis = Annotated[StrictInt, Field(ge=100000000000, le=4102444800000)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Limit = Annotated[int, Query(ge=1, le=50)]
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),
                     tz_aware=True, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]


def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="employee_department")
    db.employees.create_index([("joined_on", ASCENDING), ("department", ASCENDING)], name="employee_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING)],
                                    unique=True, name="attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", DESCENDING),
                                    ("emp_code", ASCENDING)], name="attendance_status")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="attendance_punch")


@asynccontextmanager
async def lifespan(app):
    # A failed initial connection leaves health available with 503. Once MongoDB
    # recovers, the readiness probe retries index creation before reporting ready.
    app.state.indexes_ready = False
    try:
        create_indexes()
        app.state.indexes_ready = True
    except PyMongoError:
        pass
    yield
    client.close()


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


def invalid(message):
    raise HTTPException(422, detail=[{"type": "value_error", "loc": ["request"], "msg": message}])


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        invalid("Expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        invalid("Invalid calendar date")


def month_bounds(month):
    if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        invalid("Expected YYYY-MM")
    try:
        year, number = map(int, month.split("-"))
        start = date(year, number, 1)
        end = date(year, number, calendar.monthrange(year, number)[1])
        return start, end
    except ValueError:
        invalid("Invalid month")


def instant(milliseconds=None):
    if milliseconds is None:
        return datetime.now(UTC).replace(microsecond=0)
    return datetime.fromtimestamp(milliseconds // 1000, UTC)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def serialize(value):
    if isinstance(value, datetime):
        elapsed = utc(value) - datetime(1970, 1, 1, tzinfo=UTC)
        return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    return value


def record_response(doc):
    defaults = {"punch_in": None, "punch_out": None, "work_hours": None,
                "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []}
    return serialize({**defaults, **doc})


def attendance_day(punch_in, employee):
    local = utc(punch_in).astimezone(IST)
    day = local.date()
    if employee["shift_end"] <= employee["shift_start"] and local.time() < time.fromisoformat(employee["shift_end"]):
        day -= timedelta(days=1)
    return day.isoformat()


def shift_instant(day, clock):
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock), IST)


def compute_late_minutes(punch_in, shift_start, day):
    seconds = (utc(punch_in) - shift_instant(day, shift_start)).total_seconds()
    return int(seconds // 60) if seconds > 600 else 0


def compute_work_hours(punch_in, punch_out):
    seconds = Decimal(int((utc(punch_out) - utc(punch_in)).total_seconds()))
    return float((seconds / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out, employee, day):
    end = shift_instant(day, employee["shift_end"])
    if employee["shift_end"] <= employee["shift_start"]:
        end += timedelta(days=1)
    minutes = int((utc(punch_out) - end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def derived(employee, day, status, punch_in, punch_out):
    result = {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    if status not in PRESENT:
        return result
    result["late_minutes"] = compute_late_minutes(punch_in, employee["shift_start"], day)
    if punch_out is not None:
        result["work_hours"] = compute_work_hours(punch_in, punch_out)
        result["overtime_minutes"] = compute_overtime(punch_out, employee, day)
        result["half_day"] = result["work_hours"] < 4.50
    return result


def employee_or_404(emp_code):
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_date(cls, value):
        parse_date(value)
        return value

    @model_validator(mode="after")
    def different_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Millis = Field(default_factory=lambda: int(datetime.now(UTC).timestamp()) * 1000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Millis = Field(default_factory=lambda: int(datetime.now(UTC).timestamp()) * 1000)


class RegularizeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Status = None
    punch_in: Millis = None
    punch_out: Millis = None


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        if not getattr(app.state, "indexes_ready", False):
            create_indexes()
            app.state.indexes_ready = True
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = {**body.model_dump(), "created_at": instant()}
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize(doc)


@app.get("/employees")
def list_employees(department: str | None = None, page: Page = 1, page_size: PageSize = 20):
    query = {} if department is None else {"department": department}
    items = list(db.employees.find(query, {"_id": 0}).sort("emp_code", 1)
                 .skip((page - 1) * page_size).limit(page_size))
    return {"items": serialize(items), "total": db.employees.count_documents(query),
            "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = employee_or_404(body.emp_code)
    ts = instant(body.punched_at)
    day = attendance_day(ts, employee)
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status,
           "punch_in": ts, "punch_out": None, "history": [],
           **derived(employee, day, body.status, ts, None)}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this date")
    return record_response(doc)


def validate_interval(punch_in, punch_out):
    elapsed = (utc(punch_out) - utc(punch_in)).total_seconds()
    if elapsed <= 0 or elapsed > 86400:
        invalid("punch_out must be after punch_in and within 24 hours")


def snapshot_filter(doc):
    # Compare every mutable field, including history, without adding fields to
    # the stored contract. Missing legacy values remain distinguishable from null.
    query = {"_id": doc["_id"]}
    for key in ["status", "punch_in", "punch_out", "work_hours", "late_minutes",
                "overtime_minutes", "half_day", "history"]:
        query[key] = {"$eq": doc[key], "$exists": True} if key in doc else {"$exists": False}
    return query


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = employee_or_404(body.emp_code)
    ts = instant(body.punched_at)
    doc = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": ts, "$type": "date"}},
                                       sort=[("punch_in", -1)])
    if doc is None:
        raise HTTPException(404, "No punch-in found")
    if doc.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    validate_interval(doc["punch_in"], ts)
    update = {"punch_out": ts, **derived(employee, doc["date"], doc["status"], doc["punch_in"], ts)}
    result = db.attendance_logs.find_one_and_update(snapshot_filter(doc), {"$set": update},
                                                   return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(409, "Attendance record changed concurrently")
    return record_response(result)


def attendance_query(emp_code=None, date_from=None, date_to=None, status=None):
    if date_from is not None:
        parse_date(date_from)
    if date_to is not None:
        parse_date(date_to)
    if date_from and date_to and date_from > date_to:
        invalid("date_from must not exceed date_to")
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status is not None:
        query["status"] = status
    return query


def attendance_index(query):
    if "emp_code" in query:
        return "attendance_employee_date"
    return "attendance_status" if "status" in query else "attendance_date"


@app.get("/attendance")
def list_attendance(emp_code: str | None = None, date_from: str | None = None,
                    date_to: str | None = None, status: Status | None = None,
                    page: Page = 1, page_size: PageSize = 20):
    query = attendance_query(emp_code, date_from, date_to, status)
    cursor = db.attendance_logs.find(query).hint(attendance_index(query)).sort([("date", -1), ("emp_code", 1)])
    items = [record_response(doc) for doc in cursor.skip((page - 1) * page_size).limit(page_size)]
    return {"items": items, "total": db.attendance_logs.count_documents(query, hint=attendance_index(query)),
            "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, day: Annotated[str, Path(alias="date")], body: RegularizeIn):
    parse_date(day)
    employee = employee_or_404(emp_code)
    doc = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if doc is None:
        raise HTTPException(404, "Attendance record not found")
    supplied = body.model_fields_set
    status = body.status if "status" in supplied else doc["status"]
    pi = instant(body.punch_in) if "punch_in" in supplied else doc.get("punch_in")
    po = instant(body.punch_out) if "punch_out" in supplied else doc.get("punch_out")
    if status not in PRESENT:
        if supplied & {"punch_in", "punch_out"}:
            invalid("ABSENT and LEAVE cannot include punch times")
        pi = po = None
    else:
        if pi is None:
            invalid("Presence requires punch_in")
        pi = utc(pi).replace(microsecond=0)
        po = utc(po).replace(microsecond=0) if po is not None else None
        if attendance_day(pi, employee) != day:
            invalid("punch_in must remain on the attendance date")
        if po is not None:
            validate_interval(pi, po)
    fields = {"status": status, "punch_in": pi, "punch_out": po,
              **derived(employee, day, status, pi, po)}
    defaults = {"late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    changes = {key: {"from": doc.get(key, defaults.get(key)), "to": value}
               for key, value in fields.items() if doc.get(key, defaults.get(key)) != value}
    if not changes:
        invalid("Request changes nothing")
    entry = {"at": instant(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    result = db.attendance_logs.find_one_and_update(snapshot_filter(doc),
                {"$set": fields, "$push": {"history": entry}}, return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(409, "Attendance record changed concurrently")
    return record_response(result)


# Aggregation expressions use decimal arithmetic: MongoDB $round is half-even.
def half_up(expression, places=2):
    factor = 10 ** places
    return {"$toDouble": {"$divide": [{"$floor": {"$add": [
        {"$multiply": [{"$toDecimal": expression}, factor]}, Decimal128("0.5")]}}, factor]}}


def date_expr(expression):
    return {"$dateFromString": {"dateString": expression, "format": "%Y-%m-%d", "timezone": "Asia/Kolkata"}}


def working_day(expression):
    return {"$in": [{"$dayOfWeek": {"date": expression, "timezone": "Asia/Kolkata"}}, [2, 3, 4, 5, 6]]}


def weight():
    return {"$cond": [{"$in": ["$status", PRESENT]}, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}


def metrics_group(identifier):
    return {"$group": {"_id": identifier,
        "present_days": {"$sum": {"$cond": [working_day(date_expr("$date")), weight(), 0]}},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        "hours_sum": {"$sum": {"$cond": [{"$in": ["$status", PRESENT]}, {"$toDecimal": {"$ifNull": ["$work_hours", 0]}}, 0]}},
        "hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", PRESENT]},
                                                  {"$ne": [{"$ifNull": ["$work_hours", None]}, None]}]}, 1, 0]}}}}


def monthly_pipeline(emp_code, month):
    start, end = month_bounds(month)
    employee_or_404(emp_code)
    first = date_expr({"$max": [start.isoformat(), "$joined_on"]})
    days = {"$max": [0, {"$add": [{"$dateDiff": {"startDate": first,
            "endDate": date_expr(end.isoformat()), "unit": "day", "timezone": "Asia/Kolkata"}}, 1]}]}
    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
            {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
                        "$expr": {"$eq": ["$emp_code", "$$code"]}}}, metrics_group(None)], "as": "metrics"}},
        {"$set": {"metrics": {"$ifNull": [{"$arrayElemAt": ["$metrics", 0]}, {}]},
                  "working_days": {"$size": {"$filter": {"input": {"$range": [0, days]}, "as": "offset",
                    "cond": working_day({"$dateAdd": {"startDate": first, "unit": "day", "amount": "$$offset"}})}}}}},
        {"$project": {"_id": 0, "emp_code": 1, "month": {"$literal": month}, "working_days": 1,
            **{key: {"$ifNull": ["$metrics." + key, 0]} for key in ["present_days", "leave_days", "late_count", "total_late_minutes", "total_overtime_minutes"]},
            "attendance_pct": {"$cond": [{"$gt": ["$working_days", 0]},
                half_up({"$multiply": [{"$divide": [{"$ifNull": ["$metrics.present_days", 0]}, "$working_days"]}, 100]}), None]}}}]
    return "employees", pipeline, "employee_code"


def summary_pipeline(month, department=None):
    start, end = month_bounds(month)
    query = {"joined_on": {"$lte": end.isoformat()}}
    if department is not None:
        query["department"] = department
    pipeline = [{"$match": query},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
            {"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
                        "$expr": {"$eq": ["$emp_code", "$$code"]}}}, metrics_group(None)], "as": "metrics"}},
        {"$set": {"metrics": {"$ifNull": [{"$arrayElemAt": ["$metrics", 0]}, {}]}}},
        {"$group": {"_id": "$department", "headcount": {"$sum": 1},
            **{key: {"$sum": {"$ifNull": ["$metrics." + key, 0]}} for key in
               ["present_days", "leave_days", "on_duty_count", "late_count", "total_late_minutes", "hours_sum", "hours_count"]}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "late_count": 1, "total_late_minutes": 1, "leave_count": "$leave_days", "on_duty_count": 1,
            "avg_work_hours": {"$cond": [{"$gt": ["$hours_count", 0]},
                half_up({"$divide": [{"$toDecimal": "$hours_sum"}, "$hours_count"]}), None]}}},
        {"$sort": {"department": 1}}]
    return "employees", pipeline, "employee_department" if department is not None else "employee_joined"


def leaderboard_pipeline(month, limit=10, department=None):
    start, end = month_bounds(month)
    employee_match = {} if department is None else {"employee.department": department}
    pipeline = [{"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"}, {"$match": employee_match},
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department",
                      "total_late_minutes": 1, "late_count": 1, "rank": 1}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}]
    return "attendance_logs", pipeline, "attendance_date"


def trend_pipeline(department, date_from, date_to):
    start, end = parse_date(date_from), parse_date(date_to)
    count = (end - start).days + 1
    if count < 1 or count > 92:
        invalid("Trend range must contain 1 to 92 days")
    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(404, "Department not found")
    # Generate the complete day range in MongoDB even if no attendance exists.
    # An employee anchor guarantees a row before $range/$unwind gap filling.
    pipeline = [{"$match": {"department": department}}, {"$limit": 1},
        {"$project": {"_id": 0, "offset": {"$range": [0, count]}}}, {"$unwind": "$offset"},
        {"$set": {"day": {"$dateAdd": {"startDate": date_expr(date_from), "unit": "day", "amount": "$offset"}}}},
        {"$set": {"date": {"$dateToString": {"date": "$day", "format": "%Y-%m-%d", "timezone": "Asia/Kolkata"}},
                  "is_working_day": working_day("$day")}},
        {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [
            {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},
            {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
                {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$eq": ["$date", "$$day"]}]}}},
                {"$project": {"_id": 0, "present": weight(),
                    "late": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}], "as": "logs"}},
            {"$group": {"_id": None, "headcount": {"$sum": 1},
                        "present_count": {"$sum": {"$sum": "$logs.present"}},
                        "late_count": {"$sum": {"$sum": "$logs.late"}}}}], "as": "metrics"}},
        {"$set": {"metrics": {"$ifNull": [{"$arrayElemAt": ["$metrics", 0]}, {}]}}},
        {"$set": {key: {"$ifNull": ["$metrics." + key, 0]} for key in ["headcount", "present_count", "late_count"]}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                          half_up({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4), None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {
            "$avg": {"$toDecimal": "$attendance_rate"}, "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1,
                      "late_count": 1, "attendance_rate": 1, "moving_avg_7d": half_up("$moving_avg_7d", 4)}},
        {"$sort": {"date": 1}}]
    return "employees", pipeline, "employee_department"


def aggregate(spec):
    collection, pipeline, hint = spec
    return list(db[collection].aggregate(pipeline, hint=hint, allowDiskUse=True))


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    return aggregate(monthly_pipeline(emp_code, month))[0]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: str | None = None):
    return {"month": month, "items": aggregate(summary_pipeline(month, department))}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: Limit = 10, department: str | None = None):
    return {"month": month, "items": aggregate(leaderboard_pipeline(month, limit, department))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, date_from: Annotated[str, Query(alias="from")],
                     date_to: Annotated[str, Query(alias="to")]):
    return {"department": department, "items": aggregate(trend_pipeline(department, date_from, date_to))}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
        emp_code: str | None = None, month: str | None = None, department: str | None = None,
        limit: Limit = 10, date_from: str | None = None, date_to: str | None = None,
        status: Status | None = None, from_day: Annotated[str | None, Query(alias="from")] = None,
        to_day: Annotated[str | None, Query(alias="to")] = None, page: Page = 1, page_size: PageSize = 20):
    if endpoint == "attendance_list":
        query = attendance_query(emp_code, date_from, date_to, status)
        collection = "attendance_logs"
        command = {"find": collection, "filter": query, "sort": {"date": -1, "emp_code": 1},
                   "skip": (page - 1) * page_size, "limit": page_size, "hint": attendance_index(query)}
    else:
        if endpoint == "department_trend":
            if department is None or from_day is None or to_day is None:
                invalid("department, from and to are required")
            spec = trend_pipeline(department, from_day, to_day)
        else:
            if month is None:
                invalid("month is required")
            if endpoint == "employee_monthly":
                if emp_code is None:
                    invalid("emp_code is required")
                spec = monthly_pipeline(emp_code, month)
            elif endpoint == "department_summary":
                spec = summary_pipeline(month, department)
            else:
                spec = leaderboard_pipeline(month, limit, department)
        collection, pipeline, hint = spec
        command = {"aggregate": collection, "pipeline": pipeline, "cursor": {}, "hint": hint, "allowDiskUse": True}
    result = db.command({"explain": command, "verbosity": "executionStats"})
    # Explain can contain BSON timestamps and binary plan identifiers. Extended
    # JSON preserves the raw explain document without dropping planner fields.
    import json
    from bson import json_util
    return {"endpoint": endpoint, "collection": collection, "explain": json.loads(json_util.dumps(result))}
