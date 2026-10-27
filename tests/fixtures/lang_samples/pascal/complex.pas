unit Complex;

interface

implementation

function ManyBranches(A, B, C, D, E: Boolean): Integer;
begin
  if A then
  begin
    Result := 1;
  end
  else if B then
  begin
    Result := 2;
  end
  else if C then
  begin
    Result := 3;
  end
  else if D then
  begin
    Result := 4;
  end
  else if E then
  begin
    Result := 5;
  end;

  if A and B then
  begin
    Result := 6;
  end;

  if C or D then
  begin
    Result := 7;
  end;

  if A and C and E then
  begin
    Result := 8;
  end;

  Result := 0;
end;

end.
