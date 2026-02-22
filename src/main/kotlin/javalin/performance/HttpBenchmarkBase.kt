package javalin.performance

import io.javalin.Javalin
import io.javalin.http.ExceptionHandler
import io.javalin.http.Handler
import org.openjdk.jmh.annotations.*
import java.io.ByteArrayInputStream
import java.lang.IllegalStateException
import java.lang.reflect.Method
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

    protected fun attachEndpointsTo(routes: Any) {
        // baseline
        registerGet(routes, "/hello") { it.result("Hello World") }

        // lifecycle + exception baseline
        registerBefore(routes, "/lifecycle") { it.result("A") }
        registerGet(routes, "/lifecycle") { it.result("B") }
        registerAfter(routes, "/lifecycle") { it.result("C") }
        registerGet(routes, "/exception") { throw IllegalStateException() }
        registerException(routes, Exception::class.java) { _, ctx -> ctx.status(500) }
        registerError(routes, 500) { it.result("Error") }

        // payload size scenarios
        registerGet(routes, "/payload/empty") { it.result("") }
        registerGet(routes, "/payload/text/100kb") { it.result(TEXT_100KB) }
        registerGet(routes, "/payload/text/1mb") { it.result(TEXT_1MB) }

        // json serialization scenarios
        registerGet(routes, "/payload/json/small") { it.json(JSON_SMALL) }
        registerGet(routes, "/payload/json/100kb") { it.json(JSON_100KB) }
        registerGet(routes, "/payload/json/1mb") { it.json(JSON_1MB) }

        // static-like raw byte responses
        registerGet(routes, "/payload/static/100kb") { ctx ->
            ctx.contentType("application/octet-stream")
            ctx.result(ByteArrayInputStream(STATIC_100KB))
        }
        registerGet(routes, "/payload/static/1mb") { ctx ->
            ctx.contentType("application/octet-stream")
            ctx.result(ByteArrayInputStream(STATIC_1MB))
        }

        // route-table scenarios
        registerRouteGroup(routes, "10", 10)
        registerRouteGroup(routes, "100", 100)
        registerRouteGroup(routes, "1000", 1000)
        registerRouteGroup(routes, "10000", 10000)
    }

    fun Javalin.attachEndpoints() {
        attachEndpointsTo(this)
    }

    private fun registerRouteGroup(routes: Any, group: String, count: Int) {
        for (index in 0 until count) {
            registerGet(routes, "/routes/$group/r$index") { it.result("route-$index") }
        }
    }

    private fun registerGet(target: Any, path: String, handler: Handler) {
        invokePathHandler(target, "get", path, handler)
    }

    private fun registerBefore(target: Any, path: String, handler: Handler) {
        invokePathHandler(target, "before", path, handler)
    }

    private fun registerAfter(target: Any, path: String, handler: Handler) {
        invokePathHandler(target, "after", path, handler)
    }

    private fun registerException(target: Any, exceptionType: Class<out Exception>, handler: ExceptionHandler<in Exception>) {
        if (invokeIfPresent(target, "exception", arrayOf(exceptionType, handler))) {
            return
        }
        throw IllegalStateException("Could not register exception handler on ${target.javaClass.name}")
    }

    private fun registerError(target: Any, status: Int, handler: Handler) {
        if (invokeIfPresent(target, "error", arrayOf(status, handler))) {
            return
        }
        if (invokeIfPresent(target, "error", arrayOf(status, "*", handler))) {
            return
        }
        throw IllegalStateException("Could not register error handler on ${target.javaClass.name}")
    }

    private fun invokePathHandler(target: Any, methodName: String, path: String, handler: Handler) {
        if (invokeIfPresent(target, methodName, arrayOf(path, handler))) {
            return
        }
        val varargMethod = target.javaClass.methods.firstOrNull {
            it.name == methodName &&
                it.parameterCount == 3 &&
                it.parameterTypes[0] == String::class.java &&
                it.parameterTypes[2].isArray
        }
        if (varargMethod != null) {
            val roleComponent = varargMethod.parameterTypes[2].componentType
            val emptyRoles = java.lang.reflect.Array.newInstance(roleComponent, 0)
            varargMethod.invoke(target, path, handler, emptyRoles)
            return
        }
        throw IllegalStateException("Could not register '$methodName' for path '$path' on ${target.javaClass.name}")
    }

    private fun invokeIfPresent(target: Any, methodName: String, args: Array<Any>): Boolean {
        val method = findCompatibleMethod(target.javaClass.methods.toList(), methodName, args) ?: return false
        method.invoke(target, *args)
        return true
    }

    private fun findCompatibleMethod(methods: List<Method>, methodName: String, args: Array<Any>): Method? {
        return methods.firstOrNull { method ->
            if (method.name != methodName || method.parameterCount != args.size) {
                return@firstOrNull false
            }
            method.parameterTypes.indices.all { index ->
                isTypeCompatible(method.parameterTypes[index], args[index])
            }
        }
    }

    private fun isTypeCompatible(parameterType: Class<*>, value: Any): Boolean {
        if (!parameterType.isPrimitive) {
            return parameterType.isAssignableFrom(value.javaClass)
        }
        return when (parameterType) {
            java.lang.Integer.TYPE -> value is Int
            java.lang.Long.TYPE -> value is Long
            java.lang.Boolean.TYPE -> value is Boolean
            java.lang.Double.TYPE -> value is Double
            java.lang.Float.TYPE -> value is Float
            java.lang.Short.TYPE -> value is Short
            java.lang.Byte.TYPE -> value is Byte
            java.lang.Character.TYPE -> value is Char
            else -> false
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
