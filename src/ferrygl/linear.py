"""Read and write Linear through its GraphQL API with a personal API key."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from ferrygl._http import HttpError, post_json
from ferrygl.model import Attachment, LinearIssue, Link

LINEAR_GRAPHQL = "https://api.linear.app/graphql"
URL_BATCH = 25
ISSUE_PAGE = 50

_ISSUE_FIELDS = "id identifier title state { name type } team { id } assignee { id }"


class LinearError(Exception):
    """Linear couldn't be reached, rejected the API key, or returned an error."""


@dataclass(frozen=True)
class Team:
    id: str
    key: str
    name: str


@dataclass(frozen=True)
class State:
    id: str
    name: str
    type: str


class LinearClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(timeout=30)
        # Personal API keys go in the header as they are, without "Bearer".
        self._headers = {"Authorization": api_key, "User-Agent": "ferryGL"}
        self._sleep = sleep

    def graphql(
        self, query: str, variables: dict[str, Any] | None = None, *, idempotent: bool = True
    ) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        try:
            body = post_json(
                self._http,
                LINEAR_GRAPHQL,
                payload,
                self._headers,
                sleep=self._sleep,
                idempotent=idempotent,
            )
        except HttpError as exc:
            raise LinearError(str(exc)) from None
        if errors := body.get("errors"):
            raise LinearError(_error_message(errors))
        return body["data"]

    # Reads

    def viewer(self) -> tuple[str, str]:
        """The API key owner's user ID and name."""
        viewer = self.graphql("query { viewer { id name } }")["viewer"]
        return viewer["id"], viewer["name"]

    def find_team(self, ref: str) -> Team | None:
        """A team by key, such as "ENG", or by name."""
        query = """
        query($ref: String!) {
          teams(filter: {or: [{key: {eqIgnoreCase: $ref}}, {name: {eqIgnoreCase: $ref}}]}) {
            nodes { id key name }
          }
        }"""
        nodes = self.graphql(query, {"ref": ref})["teams"]["nodes"]
        nodes.sort(key=lambda n: n["key"].lower() != ref.lower())  # an exact key match first
        return Team(**nodes[0]) if nodes else None

    def team_states(self, team_id: str) -> dict[str, State]:
        query = """
        query($id: String!) { team(id: $id) { states(first: 100) { nodes { id name type } } } }"""
        nodes = self.graphql(query, {"id": team_id})["team"]["states"]["nodes"]
        return {node["name"]: State(**node) for node in nodes}

    def find_project(self, name: str, team_id: str) -> str | None:
        """The ID of the project called ``name`` that ``team_id`` can use."""
        query = """
        query($name: String!, $team: ID!) {
          projects(first: 5, filter: {
            name: {eqIgnoreCase: $name}, accessibleTeams: {some: {id: {eq: $team}}}
          }) { nodes { id } }
        }"""
        nodes = self.graphql(query, {"name": name, "team": team_id})["projects"]["nodes"]
        return nodes[0]["id"] if nodes else None

    def find_label(self, name: str, team_id: str) -> str | None:
        """The ID of a label called ``name`` in the team, else in the workspace."""
        query = """
        query($name: String!) {
          issueLabels(first: 50, filter: {name: {eqIgnoreCase: $name}}) {
            nodes { id team { id } }
          }
        }"""
        nodes = self.graphql(query, {"name": name})["issueLabels"]["nodes"]
        for wanted in (team_id, None):
            for node in nodes:
                if (node.get("team") or {}).get("id") == wanted:
                    return node["id"]
        return None

    def attachments_for_urls(self, urls: Iterable[str]) -> dict[str, list[Attachment]]:
        """The Linear links pointing at each URL, with the issues they're on."""
        unique = list(dict.fromkeys(urls))
        found: dict[str, list[Attachment]] = {url: [] for url in unique}
        for start in range(0, len(unique), URL_BATCH):
            chunk = unique[start : start + URL_BATCH]
            params = ", ".join(f"$u{i}: String!" for i in range(len(chunk)))
            fields = " ".join(
                f"a{i}: attachmentsForURL(url: $u{i}, first: 20) "
                f"{{ nodes {{ id url title subtitle metadata issue {{ {_ISSUE_FIELDS} }} }} }}"
                for i in range(len(chunk))
            )
            data = self.graphql(
                f"query({params}) {{ {fields} }}", {f"u{i}": url for i, url in enumerate(chunk)}
            )
            for i, url in enumerate(chunk):
                found[url] = [_attachment(node) for node in data[f"a{i}"]["nodes"]]
        return found

    def assigned_issues(self, team_id: str, assignee_id: str) -> list[LinearIssue]:
        """Open and closed issues in a team assigned to a user, with descriptions and links."""
        query = f"""
        query($team: ID!, $user: ID!, $after: String) {{
          issues(first: {ISSUE_PAGE}, after: $after, filter: {{
            team: {{id: {{eq: $team}}}}, assignee: {{id: {{eq: $user}}}}
          }}) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ {_ISSUE_FIELDS} description attachments(first: 20) {{ nodes {{ url }} }} }}
          }}
        }}"""
        issues: list[LinearIssue] = []
        after = None
        while True:
            page = self.graphql(query, {"team": team_id, "user": assignee_id, "after": after})
            connection = page["issues"]
            issues.extend(_issue(node) for node in connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return issues
            after = connection["pageInfo"]["endCursor"]

    # Writes

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        state_id: str,
        assignee_id: str,
        project_id: str | None = None,
        label_ids: Iterable[str] = (),
    ) -> LinearIssue:
        query = f"""
        mutation($input: IssueCreateInput!) {{
          issueCreate(input: $input) {{ success issue {{ {_ISSUE_FIELDS} }} }}
        }}"""
        issue_input: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
            "stateId": state_id,
            "assigneeId": assignee_id,
        }
        if project_id:
            issue_input["projectId"] = project_id
        if labels := list(label_ids):
            issue_input["labelIds"] = labels
        # Not retried after a network error: Linear may have created the issue already. If
        # it did, the next sync adopts it through the GitHub URL in its description.
        payload = self.graphql(query, {"input": issue_input}, idempotent=False)["issueCreate"]
        if not payload["success"] or not payload.get("issue"):
            raise LinearError(f"Linear didn't create the issue {title!r}")
        return _issue(payload["issue"])

    def create_attachment(self, issue_id: str, link: Link) -> None:
        """Add ``link`` to an issue. Linear updates the link if the issue already has the URL."""
        query = """
        mutation($input: AttachmentCreateInput!) { attachmentCreate(input: $input) { success } }"""
        attachment_input: dict[str, Any] = {
            "issueId": issue_id,
            "url": link.url,
            "title": link.title,
            "metadata": link.metadata,
        }
        if link.subtitle:
            attachment_input["subtitle"] = link.subtitle
        self._require(self.graphql(query, {"input": attachment_input}), "attachmentCreate")

    def update_attachment(self, link: Link) -> None:
        if link.attachment_id is None:
            raise ValueError("update_attachment needs a link with attachment_id")
        query = """
        mutation($id: String!, $input: AttachmentUpdateInput!) {
          attachmentUpdate(id: $id, input: $input) { success }
        }"""
        attachment_input: dict[str, Any] = {"title": link.title, "metadata": link.metadata}
        if link.subtitle:
            attachment_input["subtitle"] = link.subtitle
        data = self.graphql(query, {"id": link.attachment_id, "input": attachment_input})
        self._require(data, "attachmentUpdate")

    def update_issue_state(self, issue_id: str, state_id: str) -> None:
        query = """
        mutation($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) { success }
        }"""
        data = self.graphql(query, {"id": issue_id, "input": {"stateId": state_id}})
        self._require(data, "issueUpdate")

    def create_comment(self, issue_id: str, body: str) -> None:
        query = """
        mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success } }"""
        # Not retried after a network error: Linear may have posted the comment already.
        variables = {"input": {"issueId": issue_id, "body": body}}
        self._require(self.graphql(query, variables, idempotent=False), "commentCreate")

    @staticmethod
    def _require(data: dict[str, Any], mutation: str) -> None:
        if not data[mutation]["success"]:
            raise LinearError(f"Linear reported {mutation} as unsuccessful")


def _issue(node: dict[str, Any]) -> LinearIssue:
    attachments = (node.get("attachments") or {}).get("nodes") or []
    return LinearIssue(
        id=node["id"],
        identifier=node["identifier"],
        title=node["title"],
        state_name=node["state"]["name"],
        state_type=node["state"]["type"],
        team_id=node["team"]["id"],
        assignee_id=(node.get("assignee") or {}).get("id"),
        description=node.get("description") or "",
        attachment_urls=tuple(a["url"] for a in attachments),
    )


def _attachment(node: dict[str, Any]) -> Attachment:
    return Attachment(
        id=node["id"],
        url=node["url"],
        title=node["title"],
        subtitle=node.get("subtitle"),
        metadata=node.get("metadata") or {},
        issue=_issue(node["issue"]),
    )


def _error_message(errors: list[dict[str, Any]]) -> str:
    messages = []
    for error in errors:
        extensions = error.get("extensions") or {}
        message = extensions.get("userPresentableMessage") or error.get("message") or "unknown"
        if extensions.get("code") == "AUTHENTICATION_ERROR":
            message = f"Linear rejected the API key: {message}"
        messages.append(message)
    return "; ".join(dict.fromkeys(messages))
