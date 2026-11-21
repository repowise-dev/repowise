add_library(B2 STATIC
    src/b2.cpp
)
target_include_directories(B2 PUBLIC include)
target_link_libraries(B2 PUBLIC C2)
