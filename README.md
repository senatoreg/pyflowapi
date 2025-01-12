# PyFlowApi

pyflowapi

pyflowapi is a framework for creating REST API services, powered by [PyFreeFlow](https://github.com/senatoreg/pyfreeflow) and [FastAPI](https://fastapi.tiangolo.com).
It allows you to configure and expose endpoints through a simple YAML configuration file, without writing additional code.

## Definition

The YAML configuration requires two main keys:

- **pyfreeflow**: defines which non-preloaded pyfreeflow extensions should be loaded.
- **api**: defines the list of API endpoints to be exposed.

## API configuration

Each API entry under the api key supports the following parameters:

- **min_size**: minimum request size.
- **max_size**: maximum request size.
- **methods**: list of allowed HTTP methods (GET, POST).
- **route**: endpoint route.
- **version (optional)**: API version, defaults to 0.0.
- **pipeline**: the pyfreeflow pipeline definition to be executed when the endpoint is called. (See pyfreeflow documentation.)

## Endpoint composition

Each endpoint is automatically exposed following the rule:

```bash
v<X>/<Y>/<route>
```

Where:

- **X** = major version number
- **Y** = minor version number
- **route** = the configured route string

Example:

```
v1/0/encrypt
```

## Example configuration

```yaml
pyfreeflow:
  ext:
  - pyfreeflow.ext.sleep_operator
api:
- max_size: 128
  methods:
  - GET
  - POST
  min_size: 4
  pipeline:
    digraph:
    - I -> A0
    - A0 -> A1
    - A1 -> A2
    - A2 -> O
    name: sleep
    node:
    - config:
        transformer: |2
          data.I = true
          state.I = true
      name: I
      type: DataTransformer
      version: '1.0'
    - config:
        transformer: |2
          data.A = true
          state.A = 0
      name: A0
      type: DataTransformer
      version: '1.0'
    - config:
        sleep_min: 0
        sleep_max: 2
      name: A1
      type: RandomSleepOperator
      version: '1.0'
    - config:
        transformer: |2
          data.A = true
          state.A = state.A + 1
      name: A2
      type: DataTransformer
      version: '1.0'
    - config:
        transformer: |2
          local result
          for k, v in pairs(state) do
            result[k] = v
          end
          data = {body = result}
      name: O
      type: DataTransformer
      version: '1.0'
  version: '1.0'
  route: /sleep
```


This configuration exposes a single endpoint:

Route: v1/0/sleep
Method: GET/POST
Pipeline: sleep, defined using pyfreeflow operators

## Quickstart

Run the API server by providing a configuration file:

```bash
pyflowapi-server -c config.yaml
```

## Reference

[PyFreeFlow](https://github.com/senatoreg/pyfreeflow) -> core library for pipeline definition and execution.
[FastAPI](https://fastapi.tiangolo.com) -> web framework powering the API layer.

# License

This software is available under dual licensing:

## Non-Commercial Use - AGPL v3
For non-commercial use, this software is released under the GNU Affero General Public License v3.0.
Any modifications must be shared under the same license.

## Commercial Use
For commercial use, please contact me.
