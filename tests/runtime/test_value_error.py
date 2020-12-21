import friendly_traceback


def test_Not_enough_values_to_unpack():
    d = (1,)
    try:
        a, b, *c = d
    except Exception as e:
        friendly_traceback.explain_traceback(redirect="capture")
    result = friendly_traceback.get_output()
    assert not "debug_warning" in result, "Internal error found."
    assert (
        "ValueError: not enough values to unpack (expected at least 2, got 1)" in result
    )
    if friendly_traceback.get_lang() == "en":
        assert "a `tuple` of length 1" in result

    d = "ab"
    try:
        a, b, c = d
    except Exception as e:
        message = str(e)
        friendly_traceback.explain_traceback(redirect="capture")
    result = friendly_traceback.get_output()
    assert not "debug_warning" in result, "Internal error found."
    assert "ValueError: not enough values to unpack (expected 3, got 2)" in result
    if friendly_traceback.get_lang() == "en":
        assert "a string (`str`) of length 2" in result
    return result, message


def test_Too_many_values_to_unpack():
    c = [1, 2, 3]
    try:
        a, b = c
    except Exception as e:
        message = str(e)
        friendly_traceback.explain_traceback(redirect="capture")
    result = friendly_traceback.get_output()
    assert not "debug_warning" in result, "Internal error found."
    assert "ValueError: too many values to unpack (expected 2)" in result
    if friendly_traceback.get_lang() == "en":
        assert "a `list` of length 3" in result
    return result, message


if __name__ == "__main__":
    print(test_Too_many_values_to_unpack()[0])
