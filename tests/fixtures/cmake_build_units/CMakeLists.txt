cmake_minimum_required(VERSION 3.16)
project(BuildUnitsFixture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)

# Third-party: linked by C1, must never become a build unit or an in-repo dep.
find_package(Threads REQUIRED)

add_subdirectory(A)
add_subdirectory(B)
add_subdirectory(C)
add_subdirectory(app)
