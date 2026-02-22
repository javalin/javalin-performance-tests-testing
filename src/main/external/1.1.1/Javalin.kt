package javalin.performance

import io.javalin.Javalin

fun main(args: Array<String>) {
    Benchmarks.run(args)
}

open class JavalinBenchmark : HttpBenchmarkBase() {
    var app: Javalin? = null

    override fun startServer(port: Int) {
        app = Javalin.start(port)
        val javalin = app!!
        attachEndpoints(
            registerGet = { path, handler -> javalin.get(path, handler) },
            registerBefore = { path, handler -> javalin.before(path, handler) },
            registerAfter = { path, handler -> javalin.after(path, handler) },
            registerException = { exceptionClass, handler -> javalin.exception(exceptionClass, handler) },
            registerError = { status, handler -> javalin.error(status, handler) },
        )
    }

    override fun stopServer() {
        app!!.stop()
    }
}
