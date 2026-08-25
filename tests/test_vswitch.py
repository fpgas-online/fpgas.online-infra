from tests.vm.vswitch import tag_frame, untag_frame

DST = bytes(6)
SRC = bytes.fromhex("525400aabb02")
PAYLOAD = b"\x08\x00" + b"x" * 46  # ethertype IPv4 + body


def test_tag_inserts_8021q_after_src_mac():
    tagged = tag_frame(DST + SRC + PAYLOAD, 2101)
    assert tagged[:12] == DST + SRC
    assert tagged[12:14] == b"\x81\x00"
    assert int.from_bytes(tagged[14:16], "big") & 0x0FFF == 2101
    assert tagged[16:] == PAYLOAD


def test_untag_roundtrip_and_foreign_vlan_dropped():
    frame = DST + SRC + PAYLOAD
    assert untag_frame(tag_frame(frame, 2101), 2101) == frame
    assert untag_frame(tag_frame(frame, 2102), 2101) is None
    assert untag_frame(frame, 2101) is None  # untagged on trunk -> drop
