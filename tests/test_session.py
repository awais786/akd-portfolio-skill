"""Trade-socket session state machine tests (protocol map §3-4).

No real socket anywhere here: SessionState is pure - it is fed raw bytes and
asked what to send next, exactly like Task 12's daemon will drive it over a
real asyncio StreamReader/Writer, but nothing here opens a connection.

All account/token/position values are synthetic (ACCT-TEST / TOK-CLIENT /
1~2~3~4), matching the redaction convention used by Task 8/9's fixtures.
"""

from akd.protocol.codec import encode_message, iter_messages
from akd.protocol.session import (
    SessionState,
    build_authenticate_partial,
    build_heartbeat,
    build_new_client,
)

ACCOUNT = "ACCT-TEST"
LOCAL_IP = "127.0.0.1"
TOKEN = "TOK-CLIENT"
POSITION_TAIL = "1~2~3~4"


def _server_ack(*fields: str) -> bytes:
    """A synthetic inbound frame - well-formed (MSG-CLIENT-prefixed) but its
    real content is a documented unknown (protocol map §3: no server->client
    message types were captured). Standing in for "the server said
    something back", never for a specific real ack payload."""
    return encode_message(["SERVER_ACK", *fields])


# --- builders: wire shape -----------------------------------------------


def test_new_client_frame_matches_the_captured_shape():
    wire = build_new_client(ACCOUNT, LOCAL_IP)
    msgs, rest = iter_messages(wire)
    assert msgs == [["MSG-CLIENT", "NEW_CLIENT", ACCOUNT, LOCAL_IP]]
    assert rest == b""


def test_authenticate_partial_frame_matches_the_captured_shape():
    wire = build_authenticate_partial(ACCOUNT, TOKEN, POSITION_TAIL)
    msgs, rest = iter_messages(wire)
    assert msgs == [
        ["MSG-CLIENT", "AUTHENTICATE_PARTIAL", ACCOUNT, TOKEN, POSITION_TAIL]
    ]
    assert rest == b""


def test_heartbeat_frame_matches_the_captured_shape():
    wire = build_heartbeat(ACCOUNT)
    msgs, rest = iter_messages(wire)
    assert msgs == [["MSG-CLIENT", "HI_FROM_CLIENT", ACCOUNT]]
    assert rest == b""


# --- feed(): the buffer/remainder discipline -----------------------------


def test_feed_accumulates_a_frame_split_across_two_reads():
    wire = build_heartbeat(ACCOUNT)
    s = SessionState(account=ACCOUNT)
    assert s.feed(wire[:5]) == []  # partial: nothing complete yet
    out = s.feed(wire[5:])  # remainder completes it
    assert out == [["MSG-CLIENT", "HI_FROM_CLIENT", ACCOUNT]]


def test_feed_reassembles_a_frame_split_mid_delimiter_field_across_two_reads():
    """Review Focus 3 / the split-mid-frame contract: a socket read can land
    anywhere, including strictly between two '|'-delimited fields deep inside
    a frame (not just near the start). Split the wire at that exact byte
    offset and assert the message is produced exactly once, complete, with no
    half-frame ever surfacing from the first feed()."""
    wire = build_authenticate_partial(ACCOUNT, TOKEN, POSITION_TAIL)
    # Split strictly inside the frame, after the first field's delimiter, in
    # the middle of a later field - not at a frame boundary and not merely
    # near the start.
    split_at = len(wire) // 2
    assert 0 < split_at < len(wire)

    s = SessionState(account=ACCOUNT)
    first = s.feed(wire[:split_at])
    assert first == []  # half a frame must never be reported as a message
    second = s.feed(wire[split_at:])
    assert second == [
        ["MSG-CLIENT", "AUTHENTICATE_PARTIAL", ACCOUNT, TOKEN, POSITION_TAIL]
    ]


def test_feed_handles_two_frames_arriving_in_one_read_and_never_duplicates():
    """The inverse shape of a split frame: two complete frames delivered in a
    single socket read must both be dispatched, in order, exactly once - and
    a subsequent empty read must not replay either of them."""
    wire = build_new_client(ACCOUNT, LOCAL_IP) + build_heartbeat(ACCOUNT)
    s = SessionState(account=ACCOUNT)
    out = s.feed(wire)
    assert out == [
        ["MSG-CLIENT", "NEW_CLIENT", ACCOUNT, LOCAL_IP],
        ["MSG-CLIENT", "HI_FROM_CLIENT", ACCOUNT],
    ]
    assert s.feed(b"") == []  # nothing left to replay


def test_feed_split_frame_then_a_second_whole_frame_preserves_order_no_loss_no_dupe():
    """Combines both shapes: frame A arrives split across two reads, frame B
    arrives whole tacked onto the second read. Each logical message must come
    out exactly once, in the right order."""
    frame_a = build_new_client(ACCOUNT, LOCAL_IP)
    frame_b = build_heartbeat(ACCOUNT)
    split_at = len(frame_a) // 2

    s = SessionState(account=ACCOUNT)
    first = s.feed(frame_a[:split_at])
    assert first == []
    second = s.feed(frame_a[split_at:] + frame_b)
    assert second == [
        ["MSG-CLIENT", "NEW_CLIENT", ACCOUNT, LOCAL_IP],
        ["MSG-CLIENT", "HI_FROM_CLIENT", ACCOUNT],
    ]


# --- handshake_step(): explicit state transitions -------------------------


def test_initial_state_is_connecting():
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    assert s.state == "connecting"


def test_happy_path_drives_connecting_through_authenticating_to_live():
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)

    # First call: nothing received yet - kicks off the handshake.
    frame1 = s.handshake_step([])
    assert frame1 == build_new_client(ACCOUNT, LOCAL_IP)
    assert s.state == "authenticating"

    # Server says something back (real ack content is an undocumented
    # unknown per protocol map §3; any well-formed inbound frame is treated
    # as sufficient to proceed) - session should now send AUTHENTICATE_PARTIAL.
    frame2 = s.handshake_step([iter_messages(_server_ack("1"))[0][0]])
    assert frame2 == build_authenticate_partial(ACCOUNT, TOKEN, POSITION_TAIL)
    assert s.state == "authenticating"  # not live yet - one more ack required

    # Second well-formed ack confirms authentication.
    frame3 = s.handshake_step([iter_messages(_server_ack("2"))[0][0]])
    assert frame3 is None
    assert s.state == "live"


def test_absent_handshake_response_never_advances_to_live():
    """No response at all (an empty messages batch, over and over) must leave
    the session waiting - never optimistically flip to live."""
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    s.handshake_step([])  # sends NEW_CLIENT -> authenticating
    assert s.state == "authenticating"

    for _ in range(5):
        assert s.handshake_step([]) is None
        assert s.state == "authenticating"


def test_malformed_handshake_response_moves_to_reconnecting_not_live():
    """A response that does not even carry the wire's own MSG-CLIENT prefix
    (garbage / a foreign byte stream) is a wrong response, not merely an
    absent one - the session must not sit in "live" having never
    authenticated, so it falls back to reconnecting instead of forging ahead."""
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    s.handshake_step([])  # -> authenticating, NEW_CLIENT sent

    garbage_message = ["NOT-MSG-CLIENT", "junk"]
    frame = s.handshake_step([garbage_message])
    assert frame is None
    assert s.state == "reconnecting"
    assert s.state != "live"


def test_reconnecting_can_restart_the_handshake_from_scratch():
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    s.handshake_step([])
    s.handshake_step([["NOT-MSG-CLIENT"]])
    assert s.state == "reconnecting"

    frame = s.handshake_step([])
    assert frame == build_new_client(ACCOUNT, LOCAL_IP)
    assert s.state == "authenticating"


def test_handshake_step_is_a_no_op_once_live():
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    s.handshake_step([])
    s.handshake_step([iter_messages(_server_ack("1"))[0][0]])
    s.handshake_step([iter_messages(_server_ack("2"))[0][0]])
    assert s.state == "live"

    assert s.handshake_step([iter_messages(_server_ack("3"))[0][0]]) is None
    assert s.state == "live"


def test_reset_forces_a_live_session_back_to_reconnecting():
    """Task 12's daemon calls this when the real TCP connection drops out from
    under an already-authenticated session - the state model must reflect
    that reality rather than keep reporting live."""
    s = SessionState(account=ACCOUNT, local_ip=LOCAL_IP, token=TOKEN,
                      position_tail=POSITION_TAIL)
    s.handshake_step([])
    s.handshake_step([iter_messages(_server_ack("1"))[0][0]])
    s.handshake_step([iter_messages(_server_ack("2"))[0][0]])
    assert s.state == "live"

    s.reset()
    assert s.state == "reconnecting"
    assert s.feed(b"") == []  # any stale buffered remainder is dropped too
