package javalin.performance

import io.javalin.http.ExceptionHandler
import io.javalin.http.Handler
import org.openjdk.jmh.annotations.*
import java.io.ByteArrayInputStream
import java.lang.IllegalStateException
import java.nio.charset.StandardCharsets

@State(Scope.Benchmark)
abstract class HttpBenchmarkBase {
    companion object {
        private const val KB = 1024
        private const val MB = 1024 * 1024

        private val TEXT_100KB = "x".repeat(100 * KB)
        private val TEXT_1MB = "x".repeat(1 * MB)

        private val JSON_SMALL = mapOf("message" to "hello", "value" to 1)
        private val JSON_100KB = mapOf("payload" to "j".repeat(100 * KB))
        private val JSON_1MB = mapOf("payload" to "j".repeat(1 * MB))

        private val STATIC_100KB = "s".repeat(100 * KB).toByteArray(StandardCharsets.UTF_8)
        private val STATIC_1MB = "s".repeat(1 * MB).toByteArray(StandardCharsets.UTF_8)
    }

    private val httpClient = OkBenchmarkClient()

    private val port = 7000
    val origin = "http://localhost:$port"

    protected fun attachEndpoints(
        registerGet: (String, Handler) -> Unit,
        registerBefore: (String, Handler) -> Unit,
        registerAfter: (String, Handler) -> Unit,
        registerException: (Class<out Exception>, ExceptionHandler<in Exception>) -> Unit,
        registerError: (Int, Handler) -> Unit,
    ) {
        // baseline
        registerGet("/hello") { it.result("Hello World") }

        // lifecycle + exception baseline
        registerBefore("/lifecycle") { it.result("A") }
        registerGet("/lifecycle") { it.result("B") }
        registerAfter("/lifecycle") { it.result("C") }
        registerGet("/exception") { throw IllegalStateException() }
        registerException(Exception::class.java) { _, ctx -> ctx.status(500) }
        registerError(500) { it.result("Error") }

        // payload size scenarios
        registerGet("/payload/empty") { it.result("") }
        registerGet("/payload/text/100kb") { it.result(TEXT_100KB) }
        registerGet("/payload/text/1mb") { it.result(TEXT_1MB) }

        // json serialization scenarios
        registerGet("/payload/json/small") { it.json(JSON_SMALL) }
        registerGet("/payload/json/100kb") { it.json(JSON_100KB) }
        registerGet("/payload/json/1mb") { it.json(JSON_1MB) }

        // static-like raw byte responses
        registerGet("/payload/static/100kb") { ctx ->
            ctx.contentType("application/octet-stream")
            ctx.result(ByteArrayInputStream(STATIC_100KB))
        }
        registerGet("/payload/static/1mb") { ctx ->
            ctx.contentType("application/octet-stream")
            ctx.result(ByteArrayInputStream(STATIC_1MB))
        }

        // route-table scenarios
        registerRouteGroup(registerGet, "10", 10)
        registerRouteGroup(registerGet, "100", 100)
        registerRouteGroup(registerGet, "1000", 1000)
        registerRouteGroup(registerGet, "10000", 10000)
    }

    private fun registerRouteGroup(registerGet: (String, Handler) -> Unit, group: String, count: Int) {
        for (index in 0 until count) {
            registerGet("/routes/$group/r$index") { it.result("route-$index") }
        }
    }

    abstract fun startServer(port: Int)
    abstract fun stopServer()

    @Setup
    fun configureServer() {
        startServer(port)
    }

    @TearDown
    fun shutdownServer() {
        stopServer()
    }

    @Setup
    fun configureClient() {
        httpClient.setup()
    }

    @TearDown
    fun shutdownClient() {
        httpClient.shutdown()
    }

    private fun load(path: String) {
        httpClient.load("${origin}${path}").use {
            val buf = ByteArray(8192)
            while (it.read(buf) != -1);
        }
    }

    @Benchmark
    fun hello() {
        load("/hello")
        load("/lifecycle")
        load("/exception")
    }

    @Benchmark
    fun payloadEmpty() {
        load("/payload/empty")
    }

    @Benchmark
    fun payload100kb() {
        load("/payload/text/100kb")
    }

    @Benchmark
    fun payload1mb() {
        load("/payload/text/1mb")
    }

    @Benchmark
    fun jsonSerializationSmall() {
        load("/payload/json/small")
    }

    @Benchmark
    fun jsonSerialization100kb() {
        load("/payload/json/100kb")
    }

    @Benchmark
    fun jsonSerialization1mb() {
        load("/payload/json/1mb")
    }

    @Benchmark
    fun staticFile100kb() {
        load("/payload/static/100kb")
    }

    @Benchmark
    fun staticFile1mb() {
        load("/payload/static/1mb")
    }

    @Benchmark
    fun routes10() {
        load("/routes/10/r9")
    }

    @Benchmark
    fun routes100() {
        load("/routes/100/r99")
    }

    @Benchmark
    fun routes1000() {
        load("/routes/1000/r999")
    }

    @Benchmark
    fun routes10000() {
        load("/routes/10000/r9999")
    }
}
