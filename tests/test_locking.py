import os


def test_exclusive_lock_protects_a_user_owned_lock_file(tmp_path):
    from zendesk_mcp_server.locking import exclusive_lock

    path = tmp_path / "lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with exclusive_lock(descriptor):
            os.write(descriptor, b"x")
    finally:
        os.close(descriptor)

    assert path.read_bytes() == b"x"
