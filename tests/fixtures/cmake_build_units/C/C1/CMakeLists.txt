add_library(C1 STATIC
    src/c1.cpp
)
target_include_directories(C1 PUBLIC include)
target_link_libraries(C1 PUBLIC Threads::Threads)
