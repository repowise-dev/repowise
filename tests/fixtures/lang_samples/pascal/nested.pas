unit Nested;

interface

implementation

function DeeplyNested(X: Integer): Integer;
var
  I: Integer;
begin
  if X > 0 then
  begin
    for I := 0 to X do
    begin
      if I mod 2 = 0 then
      begin
        while I > 0 do
        begin
          if I = 5 then
          begin
            Result := I;
            Exit;
          end;
          Dec(I);
        end;
      end;
    end;
  end;
  Result := 0;
end;

function Shallow(X: Integer): Integer;
begin
  Result := X + 1;
end;

end.
