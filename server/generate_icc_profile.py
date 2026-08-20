#!/usr/bin/env python3
import os


def get_icc_profile_path():
    profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles", "CoatedFOGRA39.icc")
    if not os.path.exists(profile_path):
        raise FileNotFoundError(f"ICC profile not found at {profile_path}")
    return profile_path


if __name__ == "__main__":
    path = get_icc_profile_path()
    size = os.path.getsize(path)
    print(f"ICC profile: {path} ({size} bytes)")
