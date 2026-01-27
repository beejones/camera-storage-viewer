import sys
from unittest.mock import patch
import pytest
from scripts.deploy.generate_bcrypt_hash import main

def test_generate_hash_basic(capsys):
    """Test generating a standard bcrypt hash."""
    test_password = "mysecretpassword"
    
    with patch("getpass.getpass", return_value=test_password):
        exit_code = main([])
        
    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out.strip()
    
    # Bcrypt hashes start with $2b$ (or $2a$ etc)
    assert output.startswith("$2")
    # Should not contain double $$ unless escaped
    assert "$$" not in output

def test_generate_hash_compose_escape(capsys):
    """Test generating a hash with double $$ for Docker Compose."""
    test_password = "mysecretpassword"
    
    with patch("getpass.getpass", return_value=test_password):
        exit_code = main(["--compose-escape"])
        
    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out.strip()
    
    # Should start with $$2 because of the escape
    assert output.startswith("$$2")
    assert "$$" in output

def test_generate_hash_custom_cost(capsys):
    """Test passing a custom cost works (verification is tricky without slow hash, but we check it runs)."""
    test_password = "quickpassword"
    
    # Use a lower cost for speed in tests if allowed by library, else default is fine.
    # The script default is 14. We'll try 4 which is min for bcrypt.
    with patch("getpass.getpass", return_value=test_password):
        exit_code = main(["--cost", "4"])
        
    assert exit_code == 0
    captured = capsys.readouterr()
    output = captured.out.strip()
    assert output.startswith("$2")

def test_empty_password_fails():
    """Test that empty password raises SystemExit."""
    with patch("getpass.getpass", return_value="   "): # stripped to empty
        with pytest.raises(SystemExit) as excinfo:
            main([])
    
    assert "Password must be non-empty" in str(excinfo.value)
