add_library(A3 STATIC
    src/a3.cpp
)
target_include_directories(A3 PUBLIC include)
target_link_libraries(A3 PUBLIC ACommon B3)
