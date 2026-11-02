import asyncio
import json

import pytest

import fastapi_app as app


VALID_COURSE = {
    "course": "  Bachelor of Science in   Data Science ",
    "description": "Prepares students to analyze data and build predictive models.",
    "strands": ["stem", "ICTECH", "STEM"],
    "features": [92, 85, 78, 95, 70, 65],
}


class CourseRequest:
    def __init__(self, payload=None, role="admin"):
        self.payload = payload
        self.session = {"user_id": 1, "role": role}

    async def json(self):
        return self.payload


class CourseCursor:
    def __init__(self, duplicate=False, existing="Old Course", course_count=5):
        self.duplicate = duplicate
        self.existing = existing
        self.course_count = course_count
        self.statements = []

    def execute(self, query, params=()):
        self.statements.append((" ".join(query.split()), params))

    def fetchone(self):
        query = self.statements[-1][0]
        if "LOWER(course) = LOWER(?)" in query:
            return (1,) if self.duplicate else None
        if "RETURNING id" in query:
            return (41,)
        if "FOR UPDATE" in query:
            return (self.existing,) if self.existing else None
        if "COUNT(*)" in query:
            return (self.course_count,)
        return None

    def fetchall(self):
        if "pg_constraint" in self.statements[-1][0]:
            return [("CHECK ((strand_code = ANY (ARRAY['BE'::text, 'STEM'::text, 'ICTech'::text, 'HT'::text, 'ARSSH'::text])))",)]
        return [(3, "BS Nursing", "Care for patients.", [70, 95, 80, 60, 55, 85], ["stem", "HT"])]


class CourseConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def catalog(monkeypatch):
    cursor = CourseCursor()
    connection = CourseConnection(cursor)
    activity = []
    monkeypatch.setattr(app, "_db_conn", lambda: connection)
    monkeypatch.setattr(app, "_is_full_admin_session", lambda sess: sess.get("role") == app.ROLE_SUPER_ADMIN)
    monkeypatch.setattr(app, "_record_admin_activity", lambda sess, action, **kwargs: activity.append((action, kwargs)))
    monkeypatch.setattr(app, "_COURSE_TRAINING_DATA_CACHE", object())
    monkeypatch.setattr(app, "_NEAREST_NEIGHBOR_MODEL", object())
    return cursor, connection, activity


def body(response):
    return json.loads(response.body)


def test_super_admin_can_list_courses(catalog):
    response = app.admin_list_courses(CourseRequest())

    assert response.status_code == 200
    assert body(response)["courses"] == [{
        "id": 3,
        "course": "BS Nursing",
        "description": "Care for patients.",
        "features": [70, 95, 80, 60, 55, 85],
        "strands": ["HT", "STEM"],
    }]


@pytest.mark.parametrize("call", [
    lambda request: app.admin_list_courses(request),
    lambda request: asyncio.run(app.admin_create_course(request)),
    lambda request: asyncio.run(app.admin_update_course(request, "3")),
    lambda request: app.admin_delete_course(request, "3"),
])
def test_semi_admin_cannot_manage_courses(catalog, call):
    response = call(CourseRequest(dict(VALID_COURSE), role="semi_admin"))

    assert response.status_code == 403
    assert catalog[0].statements == []


def test_create_course_normalizes_input_and_resets_recommendation_cache(catalog):
    cursor, connection, activity = catalog

    response = asyncio.run(app.admin_create_course(CourseRequest(dict(VALID_COURSE))))

    assert response.status_code == 200
    saved = body(response)["course"]
    assert saved == {
        "id": 41,
        "course": "Bachelor of Science in Data Science",
        "description": VALID_COURSE["description"],
        "features": [92.0, 85.0, 78.0, 95.0, 70.0, 65.0],
        "strands": ["ICTECH", "STEM"],
    }
    insert = next(params for query, params in cursor.statements if query.startswith("INSERT INTO university_courses"))
    assert json.loads(insert[1]) == saved["features"]
    strand_rows = [params for query, params in cursor.statements if query.startswith("INSERT INTO university_course_strands")]
    # The live catalog constraint spells ICTECH as "ICTech", so stored rows must match it.
    assert strand_rows == [(saved["course"], "ICTech"), (saved["course"], "STEM")]
    assert connection.committed
    assert app._COURSE_TRAINING_DATA_CACHE is None
    assert app._NEAREST_NEIGHBOR_MODEL is None
    assert activity == [("course_created", {"target_label": saved["course"]})]


@pytest.mark.parametrize("changes, message", [
    ({"course": "AB"}, "Course name"),
    ({"description": "Too short"}, "Description"),
    ({"features": [90, 90, 90, 90, 90]}, "six subject areas"),
    ({"features": [90, 90, 90, 90, 90, 101]}, "social score"),
    ({"features": [90, 90, "high", 90, 90, 90]}, "english score"),
    ({"features": [90, 90, 90, True, 90, 90]}, "technology score"),
    ({"strands": []}, "at least one"),
    ({"strands": ["GAS"]}, "supported academic clusters"),
])
def test_create_course_rejects_invalid_details(catalog, changes, message):
    response = asyncio.run(app.admin_create_course(CourseRequest({**VALID_COURSE, **changes})))

    assert response.status_code == 400
    assert message in body(response)["message"]
    assert catalog[0].statements == []


def test_create_course_rejects_case_insensitive_duplicate(catalog):
    cursor, connection, _ = catalog
    cursor.duplicate = True

    response = asyncio.run(app.admin_create_course(CourseRequest(dict(VALID_COURSE))))

    assert response.status_code == 409
    assert "already exists" in body(response)["message"]
    assert not connection.committed


def test_rename_course_rewrites_cluster_rows(catalog):
    cursor, connection, activity = catalog

    response = asyncio.run(app.admin_update_course(CourseRequest(dict(VALID_COURSE)), "3"))

    assert response.status_code == 200
    queries = [query for query, _ in cursor.statements]
    delete_index = next(i for i, query in enumerate(queries) if query.startswith("DELETE FROM university_course_strands"))
    update_index = next(i for i, query in enumerate(queries) if query.startswith("UPDATE university_courses"))
    insert_index = next(i for i, query in enumerate(queries) if query.startswith("INSERT INTO university_course_strands"))
    assert delete_index < update_index < insert_index
    assert cursor.statements[delete_index][1] == ("Old Course",)
    assert cursor.statements[update_index][1][-1] == 3
    assert connection.committed
    assert activity[0][0] == "course_updated"


def test_update_missing_course_returns_not_found(catalog):
    cursor, connection, _ = catalog
    cursor.existing = None

    response = asyncio.run(app.admin_update_course(CourseRequest(dict(VALID_COURSE)), "999"))

    assert response.status_code == 404
    assert not connection.committed


def test_delete_course_removes_clusters_and_course(catalog):
    cursor, connection, activity = catalog

    response = app.admin_delete_course(CourseRequest(), "3")

    assert response.status_code == 200
    deletes = [(query, params) for query, params in cursor.statements if query.startswith("DELETE")]
    assert deletes == [
        ("DELETE FROM university_course_strands WHERE course = ?", ("Old Course",)),
        ("DELETE FROM university_courses WHERE id = ?", (3,)),
    ]
    assert connection.committed
    assert app._NEAREST_NEIGHBOR_MODEL is None
    assert activity == [("course_deleted", {"target_label": "Old Course"})]


def test_delete_refuses_to_empty_catalog(catalog):
    cursor, connection, _ = catalog
    cursor.course_count = 1

    response = app.admin_delete_course(CourseRequest(), "3")

    assert response.status_code == 400
    assert "at least one course" in body(response)["message"]
    assert not connection.committed


@pytest.mark.parametrize("course_id", ["abc", "0", "-2"])
def test_invalid_course_id_returns_not_found(catalog, course_id):
    assert app.admin_delete_course(CourseRequest(), course_id).status_code == 404
    assert asyncio.run(app.admin_update_course(CourseRequest(dict(VALID_COURSE)), course_id)).status_code == 404


def test_course_routes_are_registered():
    routes = {(route.path, method) for route in app.api_app.routes for method in getattr(route, "methods", ())}

    assert {
        ("/admin/courses", "GET"),
        ("/admin/courses", "POST"),
        ("/admin/courses/{course_id}", "PUT"),
        ("/admin/courses/{course_id}", "DELETE"),
    } <= routes
