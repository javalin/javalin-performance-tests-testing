package javalin.performance

import org.openjdk.jmh.results.format.ResultFormatType

object Benchmarks {

    fun run(args: Array<String>) {
        runBenchmark(args)
    }

    // run benchmark with version in property
    fun runBenchmark(args: Array<String>) {
        val version = System.getProperty("version")
        val profileName = System.getProperty("profileName")
        val its = System.getProperty("iterations").toInt()
        val itTime = System.getProperty("iterationTime").toLong()
        val threads = System.getProperty("threads", "32").toInt()
        val forks = System.getProperty("forks", "1").toInt()
        val resultFormat = ResultFormatType.valueOf(System.getProperty("resultFormat", "csv").uppercase())

        benchmark(args) {
            if (!profileName.equals(""))
                profile(profileName)
            iterations = its
            iterationTime = itTime
            this.threads = threads
            this.forks = forks
            this.resultFormat = resultFormat
            resultName =
                if (profileName.isBlank())
                    version
                else
                    "$version-$profileName"
            setup()
        }
    }

    private fun BenchmarkSettings.setup() {
        val benchmarkClassName = System.getProperty("benchmarkClass", "javalin.performance.JavalinBenchmark")
        val benchmarkClass = try {
            Class.forName(benchmarkClassName)
        } catch (exception: ClassNotFoundException) {
            throw IllegalArgumentException("Benchmark class '$benchmarkClassName' was not found", exception)
        }
        add(benchmarkClass)
    }
}
