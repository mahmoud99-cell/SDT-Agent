import pytest
from temperature import celsius_to_fahrenheit, fahrenheit_to_celsius


def test_celsius_to_fahrenheit():
    assert celsius_to_fahrenheit(0) == 32.00
    assert celsius_to_fahrenheit(100) == 212.00
    assert celsius_to_fahrenheit(-40) == -40.00
    assert celsius_to_fahrenheit(-273.15) == -459.67


def test_fahrenheit_to_celsius():
    assert fahrenheit_to_celsius(32) == 0.00
    assert fahrenheit_to_celsius(212) == 100.00
    assert fahrenheit_to_celsius(-40) == -40.00
    assert fahrenheit_to_celsius(-459.67) == -273.15
