import pytest
from stock_mcp.providers.tzzb import extract_cookie_from_curl, extract_code, parse_number

def test_helpers():
    # extract_cookie_from_curl
    curl = "curl 'https://example.com' -H 'Cookie: foo=bar; baz=qux'"
    assert extract_cookie_from_curl(curl) == "foo=bar; baz=qux"
    
    # extract_code
    assert extract_code("code_510300") == "510300"
    assert extract_code(159919) == "159919"
    
    # parse_number
    assert parse_number("1,234.56%") == 1234.56
    assert parse_number(None) is None
