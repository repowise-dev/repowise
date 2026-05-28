cmake_minimum_required(VERSION 3.20)
project(coffee_sample LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)

add_subdirectory(libcoffee)
add_subdirectory(app)
add_subdirectory(tests)
