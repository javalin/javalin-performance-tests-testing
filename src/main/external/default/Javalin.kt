package javalin.performance

import io.javalin.Javalin

fun main(args: Array<String>) {
    Benchmarks.run(args)
}

open class JavalinBenchmark : HttpBenchmarkBase() {
    val app = Javalin.create()

    override fun startServer(port: Int) {
        app.start(port)
        attachEndpoints(
            registerGet = { path, handler -> app.get(path, handler) },
            registerBefore = { path, handler -> app.before(path, handler) },
            registerAfter = { path, handler -> app.after(path, handler) },
            registerException = { exceptionClass, handler -> app.exception(exceptionClass, handler) },
            registerError = { status, handler -> app.error(status, handler) },
        )
    }

    override fun stopServer() {
        app.stop()
    }
}
