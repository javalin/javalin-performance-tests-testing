package javalin.performance

import io.javalin.Javalin

fun main(args: Array<String>) {
    Benchmarks.run(args)
}

open class JavalinBenchmark : HttpBenchmarkBase() {
    lateinit var app: Javalin

    override fun startServer(port: Int) {
        app = Javalin.create { cfg ->
            val routes = cfg.routes
            attachEndpoints(
                registerGet = { path, handler -> routes.get(path, handler) },
                registerBefore = { path, handler -> routes.before(path, handler) },
                registerAfter = { path, handler -> routes.after(path, handler) },
                registerException = { exceptionClass, handler -> routes.exception(exceptionClass, handler) },
                registerError = { status, handler -> routes.error(status, handler) },
            )
        }
        app.start(port)
    }

    override fun stopServer() {
        app.stop()
    }
}
