import friendly


def test_Generic():
    try:
        open("does_not_exist")
    except FileNotFoundError as e:
        message = str(e)
        friendly.explain_traceback(redirect="capture")
    result = friendly.get_output()
    
    assert (
        "FileNotFoundError: [Errno 2] No such file or directory: 'does_not_exist'"
        in result
    )
    if friendly.get_lang() == "en":
        assert "that cannot be found is `does_not_exist`." in result
    return result, message


if __name__ == "__main__":
    print(test_file_not_found_error()[0])
