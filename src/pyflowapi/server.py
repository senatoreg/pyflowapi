import signal
import asyncio
import json
import uvicorn
import fastapi
import logging
import pyfreeflow
import copy
import re
from platform import system

if system() == "Linux":
    import uvloop


class Route():
    def __init__(self, name, config, min_size=4, max_size=8):
        self._name = name
        self._config = config
        self._max_size = max_size
        self._queue = asyncio.Queue(max_size)
        self._size = asyncio.BoundedSemaphore(max_size)
        self._pipeline_name_generator = self._pipeline_name_builder(max_size)

        self._method = {
            "GET": self._do_get,
            "POST": self._do_post,
        }

        self._init_queue(min_size)

    def _get_pipeline_name(self):
        return next(self._pipeline_name_generator)

    def _pipeline_name_builder(self, max_size):
        for i in range(max_size):
            yield self._name + "[" + str(i) + "]"

    def _init_pipe(self, pipelina_name):
        return pyfreeflow.pipeline.Pipeline(self._config.get("node"),
                                          self._config.get("digraph"),
                                          last=self._config.get("last"), name=pipelina_name)

    def _init_queue(self, min_size):
        try:
            for i in range(min_size):
                self._queue.put_nowait(self._init_pipe(self._get_pipeline_name()))
        except asyncio.QueueFull as ex:
            raise ex

    async def _acquire(self):
        await self._size.acquire()
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return self._init_pipe(self._get_pipeline_name())
        return await self._queue.get()

    async def _release(self, pipe):
        try:
            self._queue.put_nowait(pipe)
        except asyncio.QueueFull:
            pass
        finally:
            self._size.release()

    async def _run_pipe(self, data):
        pipe = await self._acquire()
        rep = await pipe.run(data=data)
        await self._release(pipe)

        return rep

    async def _do_get(self, request, response):
        h = dict(request.headers)
        p = dict(request.query_params)
        pyfreeflow.utils.deepupdate(p, dict(request.path_params))
        client = list(request.client)

        rep = await self._run_pipe(data={"headers": h, "param": p,
                                         "client": client})

        if rep[1] != 0:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Requested process failed")

        for k, v in rep[0].get("headers", {}).items():
            response.headers[k] = v

        return rep[0].get("body")

    async def _do_post(self, request, response):
        h = dict(request.headers)
        b = await request.body()
        p = json.loads(b.decode("utf-8"))
        pyfreeflow.utils.deepupdate(p, dict(request.path_params))
        client = list(request.client)

        rep = await self._run_pipe(data={"headers": h, "param": p,
                                         "client": client})

        if rep[1] != 0:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Requested process failed")

        for k, v in rep[0].get("headers", {}).items():
            response.headers[k] = v

        return rep[0].get("body")

    async def close(self):
        while not self._queue.empty():
            pipe = await self._queue.get()
            del pipe
        for _ in range(self._max_size - self._size._value):
            self._size.release()

    async def run(self, request: fastapi.Request, response: fastapi.Response):
        method = request.method
        return await self._method[method](request, response)


class Server():
    DEFAULT_CONFIG = {
        "address": None,
        "port": 1979,
        "log": {
            "level": "info",
        },
        "ssl": {
            "enabled": False,
        },
        "api": [],
    }

    LOGGING_LEVELS = {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "trace": logging.DEBUG,
    }

    def __init__(self, config={}):
        if system() == "Linux":
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        #
        # Load default configuration
        #
        self.config = copy.deepcopy(self.DEFAULT_CONFIG)

        #
        # Get log level
        #
        self._loglevel = self.config.get("log").get("level")

        #
        # Forward flowapi log level to pyfreeflow
        #
        pyfreeflow.set_loglevel(self.LOGGING_LEVELS[self._loglevel])

        #
        # set Uvicorn logging format compliant to pyfreeflow
        #
        uvicorn_logformatters = uvicorn.config.LOGGING_CONFIG["formatters"]
        uvicorn_logformatters["default"]["fmt"] = pyfreeflow.get_logformat()
        uvicorn_logformatters["access"]["fmt"] = pyfreeflow.get_logformat()

        #
        # Load configuration if any
        #
        pyfreeflow.utils.deepupdate(self.config, config)

        pyfreeflow_config = config.get("pyfreeflow", {})
        for pyfreeflow_ext in pyfreeflow_config.get("ext", []):
            pyfreeflow.load_extension(pyfreeflow_ext)

        self._deps = {}
        self._route = {}
        self.app = fastapi.FastAPI()
        self._logger = logging.getLogger(
            ".".join([__name__, self.__class__.__name__]))

        self._logger.setLevel(self.LOGGING_LEVELS[self._loglevel])
        self._logger.info("Running event loop: {}".format(asyncio.get_event_loop()))

        self._init_context()

    def _init_context(self):
        version_splitter = re.compile(r"\.").split
        path_join = "/".join

        for dep in self.config.get("dependencies", []):
            route = Route(dep.get("name"), dep.get("pipeline"), min_size=dep.get("min_size", 4),
                          max_size=dep.get("max_size", 8))
            self._deps[dep.get("name")] = route

        for api in self.config.get("api", []):
            version = "v" + path_join(version_splitter(api.get("version", "0.0")))
            path = "/" + version + api.get("route")

            route = Route(path, api.get("pipeline"), min_size=api.get("min_size", 4),
                          max_size=api.get("max_size", 8))
            self._route[path] = route

            deps = [fastapi.Depends(self._deps[x].run) for x in api.get("depends", [])]
            self.app.add_api_route(path, route.run,
                                   methods=api.get("methods", ["GET", "POST"]),
                                   dependencies=deps)

    async def _fini_context(self):
        await self._server.shutdown()

        for k in self._route.keys():
            self.app.delete(k)

        for r in self._route.values():
            await r.close()

        tasks = [task for task in asyncio.all_tasks() if task is not
                 asyncio.current_task()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        self._logger.debug('finished awaiting cancelled tasks, results: {0}'.format(results))

    def __enter__(self):
        return self

    def __exit__(self, exc_typ, exc_val, exc_tb):
        pass

    def _stop(self, t):
        self._loop.stop()

    def _signal_handler(self):
        t = self._loop.create_task(self._fini_context())
        t.add_done_callback(self._stop)

    async def _serve(self):
        #
        # Set server up
        #
        config = uvicorn.Config(self.app, host=self.config.get("address"),
                                port=self.config.get("port"),
                                log_level=self._loglevel, access_log=True,
                                proxy_headers=True)

        config.ssl_certfile = self.config.get("ssl").get("cert")
        config.ssl_keyfile = self.config.get("ssl").get("key")

        self._server = uvicorn.Server(config)
        await self._server.serve()

    def run(self):
        self._loop.add_signal_handler(signal.SIGTERM, self._signal_handler)
        self._loop.add_signal_handler(signal.SIGINT, self._signal_handler)

        self._task = self._loop.create_task(self._serve())
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
            self._logger.info("Completed.")
