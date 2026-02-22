package javalin.performance

import io.javalin.Javalin

fun main(args: Array<String>) {
    Benchmarks.run(args)
}

open class JavalinBenchmark : HttpBenchmarkBase() {
    lateinit var app: Javalin

    override fun startServer(port: Int) {
        app = Javalin.create { cfg ->
            attachEndpointsTo(cfg.routes)
        }
        app.start(port)
    }

    override fun stopServer() {
        app.stop()
    }
}
