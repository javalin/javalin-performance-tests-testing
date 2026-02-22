package javalin.performance

import okhttp3.*
import org.apache.http.client.methods.*
import org.apache.http.impl.client.*
import java.io.*
import java.net.*
import java.util.concurrent.TimeUnit

interface BenchmarkHttpClient {
    fun setup()
    fun shutdown()
    fun load(url: String): InputStream
}

class UrlBenchmarkClient : BenchmarkHttpClient {
    override fun setup() {}
    override fun shutdown() {}
    override fun load(url: String) = URL(url).openConnection().inputStream
}

class ApacheBenchmarkClient : BenchmarkHttpClient {
    var httpClient: CloseableHttpClient? = null
    override fun setup() {
        val builder = HttpClientBuilder.create()
        httpClient = builder.build()
    }

    override fun shutdown() {
        httpClient!!.close()
        httpClient = null
    }

    override fun load(url: String): InputStream {
        val httpGet = HttpGet(url)
        val response = httpClient!!.execute(httpGet)
        return response.entity.content
    }
}

class OkBenchmarkClient : BenchmarkHttpClient {
    var httpClient: OkHttpClient? = null

    private fun timeoutProperty(name: String, defaultValue: Long): Long {
        val value = System.getProperty(name)?.toLongOrNull() ?: return defaultValue
        return if (value < 0) defaultValue else value
    }

    override fun setup() {
        val connectTimeoutMs = timeoutProperty("benchmark.http.connectTimeoutMs", 15_000L)
        val readTimeoutMs = timeoutProperty("benchmark.http.readTimeoutMs", 120_000L)
        val writeTimeoutMs = timeoutProperty("benchmark.http.writeTimeoutMs", 120_000L)
        httpClient = OkHttpClient.Builder()
            .connectTimeout(connectTimeoutMs, TimeUnit.MILLISECONDS)
            .readTimeout(readTimeoutMs, TimeUnit.MILLISECONDS)
            .writeTimeout(writeTimeoutMs, TimeUnit.MILLISECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    override fun shutdown() {
        httpClient = null
    }

    override fun load(url: String): InputStream {
        val request = Request.Builder().url(url).build()
        val response = httpClient!!.newCall(request).execute()
        return response.body().byteStream()
    }
}
