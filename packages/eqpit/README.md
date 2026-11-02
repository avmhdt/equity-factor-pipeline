# eqpit

The reusable point-in-time data layer of `equity-factor-pipeline`. Every row carries `available_at`,
the UTC instant it became public, and `PitStore` is the only read path.

Install from git:

```bash
uv add "eqpit @ git+https://github.com/avmhdt/equity-factor-pipeline#subdirectory=packages/eqpit"
```
