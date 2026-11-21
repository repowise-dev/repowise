add_library(B1 STATIC
    src/b1.cpp
)
target_include_directories(B1 PUBLIC include)
target_link_libraries(B1 PUBLIC C1)
