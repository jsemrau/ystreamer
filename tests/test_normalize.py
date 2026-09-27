from music.normalize import key_usage_error, replace_harsh_instruments, rewrite_scale_roots


def test_rewrites_scale_with_root():
    code, notes = rewrite_scale_roots('d1 $ n (scale "g minor" "0 2 4 <7 3>") # s "superpiano"')
    assert code == 'd1 $ n ((scale "minor" "0 2 4 <7 3>" + 7)) # s "superpiano"'
    assert notes


def test_rewrites_accidentals_and_parenthesised_args():
    code, _ = rewrite_scale_roots('n (scale "Eb dorian" ("0 2" + "<0 3>"))')
    assert code == 'n ((scale "dorian" ("0 2" + "<0 3>") + 3))'
    code, _ = rewrite_scale_roots('n (scale "c minor" "0 3")')
    assert code == 'n (scale "minor" "0 3")'
    code, _ = rewrite_scale_roots('n (scale "F# minor pentatonic" "0 1 2")')
    assert code == 'n ((scale "minPent" "0 1 2" + 6))'


def test_leaves_valid_scales_alone():
    for good in ['n (scale "minor" "0 2")', 'n (scale "dorian" "0 2" + 2)', 'n (scale "majPent" "0")']:
        assert rewrite_scale_roots(good)[0] == good


def test_fixes_invalid_bare_names():
    assert rewrite_scale_roots('n (scale "pentatonic" "0 1")')[0] == 'n (scale "majPent" "0 1")'


def test_replaces_harsh_synths():
    code, notes = replace_harsh_instruments('d1 $ n "0" # s "supersaw" # sound "superzow:2 bd"')
    assert code == 'd1 $ n "0" # s "ys_organ" # sound "ys_organ bd"'


def test_key_usage_error():
    assert key_usage_error('d2 $ n "0 3 5 7" # s "superpiano"')
    assert key_usage_error('d2 $ n (scale "minor" "0 3") # s "superpiano"') is None
    assert key_usage_error('d1 $ s "bd*4" # n "1"') is None
