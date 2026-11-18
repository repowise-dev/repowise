plugins { java; jacoco }
tasks.jacocoTestReport { reports { xml.required = true } }
