add_library(B3 STATIC
    src/b3.cpp
)
target_include_directories(B3 PUBLIC include)
target_link_libraries(B3 PUBLIC C3)
