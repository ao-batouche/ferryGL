from __future__ import annotations

import json

import httpx
import pytest

from ferrygl.linear import URL_BATCH, LinearClient, LinearError
from ferrygl.model import Link

ISSUE = {
    "id": "i1",
    "identifier": "ENG-1",
    "title": "Thing",
    "state": {"name": "Todo", "type": "unstarted"},
    "team": {"id": "t1"},
    "assignee": {"id": "u1"},
}


def client_for(handler) -> LinearClient:
    return LinearClient("lin_key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_attachments_for_urls_batches_requests_and_parses_issues():
    urls = [f"https://github.com/acme/api/issues/{n}" for n in range(URL_BATCH + 5)]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "lin_key"
        variables = json.loads(request.content)["variables"]
        requests.append(variables)
        data = {}
        for alias, url in variables.items():
            nodes = []
            if url == urls[0]:
                nodes = [
                    {
                        "id": "a1",
                        "url": url,
                        "title": "Issue #0",
                        "subtitle": "acme/api",
                        "metadata": {"ferrygl": {"state": "issue:open"}},
                        "issue": ISSUE,
                    }
                ]
            data["a" + alias[1:]] = {"nodes": nodes}
        return httpx.Response(200, json={"data": data})

    found = client_for(handler).attachments_for_urls(urls + urls[:1])

    assert [len(r) for r in requests] == [URL_BATCH, 5]
    assert len(found) == len(urls)
    [attachment] = found[urls[0]]
    assert attachment.issue.identifier == "ENG-1"
    assert attachment.issue.assignee_id == "u1"
    assert attachment.metadata["ferrygl"]["state"] == "issue:open"


def test_authentication_errors_say_the_key_was_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        error = {
            "message": "Authentication required",
            "extensions": {"code": "AUTHENTICATION_ERROR"},
        }
        return httpx.Response(400, json={"errors": [error]})

    with pytest.raises(LinearError, match="rejected the API key: Authentication required"):
        client_for(handler).viewer()


def test_create_issue_sends_only_the_fields_it_has():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"]["input"])
        payload = {"issueCreate": {"success": True, "issue": ISSUE}}
        return httpx.Response(200, json={"data": payload})

    client = client_for(handler)
    client.create_issue(team_id="t1", title="A", description="d", state_id="s1", assignee_id="u1")
    client.create_issue(
        team_id="t1",
        title="B",
        description="d",
        state_id="s1",
        assignee_id="u1",
        project_id="p1",
        label_ids=["l1"],
    )

    assert "projectId" not in sent[0] and "labelIds" not in sent[0]
    assert sent[1]["projectId"] == "p1" and sent[1]["labelIds"] == ["l1"]


def test_create_attachment_sends_metadata():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"]["input"])
        return httpx.Response(200, json={"data": {"attachmentCreate": {"success": True}}})

    link = Link(url="https://x/1", title="PR #1", subtitle="acme/api", metadata={"ferrygl": {}})
    client_for(handler).create_attachment("i1", link)

    assert sent == [
        {
            "issueId": "i1",
            "url": "https://x/1",
            "title": "PR #1",
            "subtitle": "acme/api",
            "metadata": {"ferrygl": {}},
        }
    ]


def test_find_label_prefers_the_team_label_over_the_workspace_label():
    nodes = [{"id": "workspace", "team": None}, {"id": "team", "team": {"id": "t1"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"issueLabels": {"nodes": nodes}}})

    client = client_for(handler)

    assert client.find_label("ferryGL", "t1") == "team"
    assert client.find_label("ferryGL", "t2") == "workspace"


def test_reads_are_retried_but_issue_creation_is_not():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["query"])
        if len(calls) == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"data": {"viewer": {"id": "u1", "name": "A"}}})

    client = LinearClient(
        "k", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None
    )
    assert client.viewer() == ("u1", "A")
    assert len(calls) == 2

    calls.clear()
    with pytest.raises(LinearError, match="HTTP 503"):
        client.create_issue(team_id="t", title="A", description="", state_id="s", assignee_id="u")
    assert len(calls) == 1


def test_unsuccessful_mutations_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"issueUpdate": {"success": False}}})

    with pytest.raises(LinearError, match="issueUpdate"):
        client_for(handler).update_issue_state("i1", "s1")
