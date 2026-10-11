add_executable(coffee_tests brew_test.cc)
target_link_libraries(coffee_tests PRIVATE coffee gtest gtest_main)
