# OpenGL Definition Files Generator by Python3

## Usage
```bash
python3 glparse.py
```

It will parse `gl.xml`, `glcore.h` and `glcore_arb.h` into `glcore.json`, then generates `glcore.hpp`, `glcode.cpp`, `glcore.cs`, `glcore.rs`.
- `glcode.cpp` and `glcode.hpp` is for C++.
- `glcore.cs` is for C#.
- `glcore.rs` is for Rust.
- `glcore.json` is for you to parse it into your language.
