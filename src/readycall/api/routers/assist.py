"""The customer's paired screen (`D120`).

Two audiences, deliberately in one file because they are two ends of one mechanism and
splitting them makes it easy to change one without the other.

* `/v1/assist/...` — **the customer**, authenticated by the token in the link and nothing
  else. No cookie, no account needed to look at a comparison.
* the agent half lives in `agent.py`, behind the staff session, because pushing is a staff
  action and `D4`'s rule is that a client never names who it is.

The token is in the **path**, which is a deliberate trade. It is a bearer credential, so
it will appear in the customer's browser history — acceptable for a value that dies with
the call and grants only what a link-tapper may see. A query string would be worse (`D14`:
never put anything sensitive in a query), and a POST-only exchange would mean the customer
cannot simply tap a link, which is the entire point.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from readycall.api.deps import ContainerDep
from readycall.api.schemas import ApiModel
from readycall.domain.enums import CallState
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.services.assist import AssistSession, AssistTier

log = get_logger(__name__)

router = APIRouter(prefix="/v1/assist", tags=["assist"])


class AssistItemOut(ApiModel):
    item_id: str
    kind: str
    title_th: str
    payload: dict[str, Any]
    responded: bool = False
    response: dict[str, Any] | None = None
    #: Rendered as a visible notice rather than silently (`D115`): a stub that looks
    #: exactly like the real thing is how a demo becomes a claim nobody meant to make.
    stub: bool = False


class AssistScreenOut(ApiModel):
    """Everything the paired screen renders. One shape, polled."""

    call_active: bool
    tier: str
    #: What the customer is invited to do next, in their own words. The screen should
    #: never show a bare "sign in" with no reason attached to it.
    prompt_th: str
    items: tuple[AssistItemOut, ...] = ()


class AssistSignInRequest(ApiModel):
    #: DEMO ONLY, the same shape as the customer simulator's persona picker (`D47`). A
    #: real deployment reaches `VERIFIED` through the app's own login, and this endpoint
    #: is refused unless `demo_login_enabled`.
    customer_id: str


class AssistRespondRequest(ApiModel):
    item_id: str
    response: dict[str, Any]


#: A call somebody is actually on. Asked of the CALL's own state rather than of the
#: transcript buffer or the open-offer table, both of which the first version used and
#: both of which are empty during a live call for their own good reasons - the transcript
#: is flushed on accept (`D106`) and the offer is resolved by it.
#:
#: ⚠️ `WRAP_UP` is deliberately NOT here (`B37`). After-call work is the agent's paperwork
#: and can run for minutes; the customer hung up when the media stopped. Counting it as
#: live left their screen claiming a conversation that had already ended.
_LIVE_STATES = {CallState.OFFERED, CallState.IN_CALL}


async def _screen(container: Any, session: AssistSession) -> AssistScreenOut:
    call = await container.calls.get(session.call_session_id)
    active = call is not None and call.state in _LIVE_STATES
    if session.tier is AssistTier.VERIFIED:
        prompt = "เจ้าหน้าที่สามารถส่งข้อมูลและแบบฟอร์มมาที่หน้าจอนี้ได้แล้ว"
    else:
        # Phrased as an OPTION, not an instruction. The guest tier is a full experience -
        # comparisons, product information, checklists and a blank quote form all arrive
        # without an account (`D120`, `D121`) - so a prompt that reads "log in to be
        # helped" describes a product we deliberately did not build.
        prompt = "ถ้าเข้าสู่ระบบ เจ้าหน้าที่จะช่วยกรอกข้อมูลของคุณล่วงหน้าได้ ไม่เข้าสู่ระบบก็รับข้อมูลทั่วไปได้ตามปกติ"
    return AssistScreenOut(
        call_active=active,
        tier=str(session.tier),
        prompt_th=prompt,
        items=tuple(
            AssistItemOut(
                item_id=i.item_id,
                kind=str(i.kind),
                title_th=i.title_th,
                payload=i.payload,
                responded=i.response is not None,
                response=i.response,
                stub=i.stub,
            )
            for i in session.items
        ),
    )


def _pair(container: Any, token: str) -> AssistSession:
    try:
        return container.assist.pair(token)  # type: ignore[no-any-return]
    except PermanentError as exc:
        # 404 rather than 401: whether a token ever existed is not information a stranger
        # holding a stale link is owed.
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{token}", response_model=AssistScreenOut)
async def screen(token: str, container: ContainerDep) -> AssistScreenOut:
    """What is on the customer's screen right now. Polled by the page.

    Polling rather than a socket, and that is a considered choice: this screen is a *view*
    of what the broker pushed, a one-second poll is invisible to a human, and the customer
    may be on mobile data behind a proxy that eats WebSockets. The agent side keeps its
    socket because it carries the live transcript, where the budget is 1.5 s (`D105`).
    """
    return await _screen(container, _pair(container, token))


@router.post("/{token}/sign-in", response_model=AssistScreenOut)
async def sign_in(
    token: str, body: AssistSignInRequest, container: ContainerDep
) -> AssistScreenOut:
    """Reach `VERIFIED`, so personal things may be pushed (`D120`).

    Tapping a link proves possession of a phone; `D42`'s whole argument is that possession
    is not identity. This is the only route to the tier that unlocks a prefilled form.
    """
    if not container.settings.demo_login_enabled:
        raise HTTPException(status_code=404, detail="not found")
    session = container.assist.sign_in(token, customer_id=body.customer_id)
    return await _screen(container, session)


@router.post("/{token}/respond", response_model=AssistScreenOut)
async def respond(
    token: str, body: AssistRespondRequest, container: ContainerDep
) -> AssistScreenOut:
    """The customer sends something back — a filled form, an acknowledgement."""
    try:
        container.assist.respond(token, item_id=body.item_id, response=body.response)
    except PermanentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _screen(container, _pair(container, token))


__all__ = ["router"]
