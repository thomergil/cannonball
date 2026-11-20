import struct

from cannonball import canon


def c1ab(handle, size, path, fmt=0x3801):
    body = struct.pack("<6I", handle, fmt, 0x20, size, 0, handle - 2) + path.encode() + b"\0"
    return struct.pack("<II", 8 + len(body), 0xC1AB) + body


def test_object_record_from_protocol_md():
    # The example in epsimatic's PROTOCOL.md section 5.2.
    data = struct.pack("<IIII", 16, 0xC189, 0xD1A6, 1) + c1ab(0x91903B82, 3832873, "B:/DCIM/100CANON/IMG_0952.JPG") + struct.pack("<II", 8, 0)
    assert canon.objects(data) == [canon.ObjectRecord(0x91903B82, 3832873, "B:/DCIM/100CANON/IMG_0952.JPG", 0x3801)]


def test_events_stop_at_garbage():
    good = c1ab(0x91900010, 2, "a")
    assert len(list(canon.events(good + struct.pack("<II", 4, 0) + good))) == 1  # length < 8
    assert len(list(canon.events(good + struct.pack("<II", 999, 0)))) == 1  # runs past the end
    assert list(canon.events(b"")) == [] and list(canon.events(b"\0" * 7)) == []


def test_short_object_record_is_skipped():
    body = struct.pack("<5I", 1, 2, 3, 4, 5)
    assert canon.objects(struct.pack("<II", 8 + len(body), 0xC1AB) + body) == []


def info(name="IMG_0952.JPG", t1="20260126T214631.0", t2="20260126T214630.0", size=3832873):
    def p16(s):
        return bytes([len(s) + 1]) + (s + "\0").encode("utf-16le")

    return struct.pack("<3I", 0x20001, 0x3801, size).ljust(0x38, b"\0") + p16(name) + p16(t1) + p16(t2)


def test_object_info():
    assert canon.object_info(info().ljust(158, b"\0")) == canon.ObjectInfo("IMG_0952.JPG", 3832873, 0x3801, "20260126T214631.0", "20260126T214630.0")


def test_object_info_without_timestamps():
    data = info()
    cut = 0x38 + 1 + 2 * len("IMG_0952.JPG\0")
    assert canon.object_info(data[:cut]) == canon.ObjectInfo("IMG_0952.JPG", 3832873, 0x3801, "", "")


def test_object_info_truncated():
    assert canon.object_info(b"") is None
    assert canon.object_info(info()[:0x38]) is None
    assert canon.object_info(info()[: 0x38 + 5]) is None  # name cut short
    assert canon.object_info(info()[:-3]) is None  # second timestamp cut short


def test_overlong_file_name_is_replaced():
    assert canon.file_name(0x2A, "x" * 129 + ".JPG") == "IMG_0000002A"
    assert canon.file_name(0x2A, "x" * 124 + ".JPG") == "x" * 124 + ".JPG"
